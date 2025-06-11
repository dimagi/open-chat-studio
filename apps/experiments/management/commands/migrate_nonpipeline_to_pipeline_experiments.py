from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.experiments.models import Experiment
from apps.pipelines.flow import FlowNode, FlowNodeData
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.nodes import AssistantNode, LLMResponseWithPrompt
from apps.teams.models import Flag


class Command(BaseCommand):
    help = "Convert assistant and LLM experiments to pipeline experiments"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be converted without making changes",
        )
        parser.add_argument(
            "--team-slug",
            type=str,
            help="Only convert experiments for a specific team (by slug)",
        )
        parser.add_argument(
            "--experiment-id",
            type=int,
            help="Convert only a specific experiment by ID",
        )
        parser.add_argument(
            "--chatbots-flag-only",
            action="store_true",
            help='Only convert experiments for teams that have the "flag_chatbots" feature flag enabled',
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        team_slug = options.get("team_slug")
        experiment_id = options.get("experiment_id")
        chatbots_flag_only = options["chatbots_flag_only"]

        query = Q(pipeline__isnull=True) & (Q(assistant__isnull=False) | Q(llm_provider__isnull=False))

        if team_slug:
            query &= Q(team__slug=team_slug)

        if experiment_id:
            query &= Q(id=experiment_id)

        if chatbots_flag_only:
            chatbots_flag_team_ids = self._get_chatbots_flag_team_ids()
            if not chatbots_flag_team_ids:
                self.stdout.write(self.style.WARNING('No teams found with the "flag_chatbots" feature flag enabled.'))
                return
            query &= Q(team_id__in=chatbots_flag_team_ids)
            self.stdout.write(f"Filtering to teams with 'flag_chatbots' FF ({len(chatbots_flag_team_ids)} teams)")

        default_experiments = Experiment.objects.filter(query & Q(is_default_version=True))
        default_working_version_ids = default_experiments.exclude(working_version__isnull=True).values_list(
            "working_version_id", flat=True
        )

        working_experiments = Experiment.objects.filter(query & Q(working_version__isnull=True)).exclude(
            id__in=default_working_version_ids
        )
        combined_ids = list(default_experiments.union(working_experiments).values_list("id", flat=True))

        experiments_to_convert = Experiment.objects.filter(id__in=combined_ids).select_related(
            "team", "assistant", "llm_provider", "llm_provider_model"
        )

        if not experiments_to_convert.exists():
            self.stdout.write(self.style.WARNING("No matching experiments found."))
            return

        self.stdout.write(f"Found {experiments_to_convert.count()} experiments to migrate:")

        for experiment in experiments_to_convert:
            experiment_type = self._get_experiment_type(experiment)
            team_info = f"{experiment.team.name} ({experiment.team.slug})"
            self.stdout.write(f"{experiment.name} (ID: {experiment.id}) - Type: {experiment_type} - Team: {team_info}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run - no changes will be made."))
            return

        confirm = input("\nContinue? (y/N): ")
        if confirm.lower() != "y":
            self.stdout.write("Cancelled.")
            return

        converted_count = 0
        failed_count = 0

        for experiment in experiments_to_convert:
            try:
                with transaction.atomic():
                    self._convert_experiment(experiment)
                    converted_count += 1
                    self.stdout.write(self.style.SUCCESS(f"Success: {experiment.name}"))
            except Exception as e:
                failed_count += 1
                self.stdout.write(self.style.ERROR(f"FAILED {experiment.name}: {str(e)}"))

        self.stdout.write(
            self.style.SUCCESS(f"\nMigration is complete!: {converted_count} succeeded, {failed_count} failed")
        )

    def _get_experiment_type(self, experiment):
        if experiment.assistant:
            return "Assistant"
        elif experiment.llm_provider:
            return "LLM"
        else:
            return "Unknown"

    def _convert_experiment(self, experiment):
        if experiment.assistant:
            pipeline = self._create_assistant_pipeline(experiment)
        elif experiment.llm_provider:
            pipeline = self._create_llm_pipeline(experiment)
        else:
            raise ValueError(f"Unknown experiment type for experiment {experiment.id}")

        experiment.pipeline = pipeline
        experiment.assistant = None
        experiment.llm_provider = None
        experiment.llm_provider_model = None

        experiment.save()

    def _get_chatbots_flag_team_ids(self):
        chatbots_flag = Flag.objects.get(name="flag_chatbots")
        return list(chatbots_flag.teams.values_list("id", flat=True))

    def _create_pipeline_with_node(self, experiment, node_type, node_label, node_params):
        """Create a pipeline with start -> custom_node -> end structure."""
        pipeline_name = f"{experiment.name} Pipeline"

        node = FlowNode(
            id=str(uuid4()),
            type="pipelineNode",
            position={"x": 400, "y": 200},
            data=FlowNodeData(
                id=str(uuid4()),
                type=node_type,
                label=node_label,
                params=node_params,
            ),
        )

        return Pipeline._create_pipeline_with_nodes(team=experiment.team, name=pipeline_name, middle_node=node)

    def _create_llm_pipeline(self, experiment):
        """Create a start -> LLMResponseWithPrompt -> end nodes pipeline for an LLM experiment."""
        llm_params = {
            "name": "llm",
            "llm_provider_id": experiment.llm_provider.id,
            "llm_provider_model_id": experiment.llm_provider_model.id,
            "llm_temperature": experiment.temperature,
            "history_type": "global",
            "history_name": None,
            "history_mode": "summarize",
            "user_max_token_limit": experiment.llm_provider_model.max_token_limit,
            "max_history_length": 10,
            "source_material_id": experiment.source_material.id if experiment.source_material else None,
            "prompt": experiment.prompt_text or "",
            "tools": list(experiment.tools) if experiment.tools else [],
            "custom_actions": [
                op.get_model_id(False)
                for op in experiment.custom_action_operations.select_related("custom_action").all()
            ],
            "built_in_tools": [],
            "tool_config": {},
        }
        return self._create_pipeline_with_node(
            experiment=experiment, node_type=LLMResponseWithPrompt.__name__, node_label="LLM", node_params=llm_params
        )

    def _create_assistant_pipeline(self, experiment):
        """Create a start -> AssistantNode -> end nodes pipeline for an assistant experiment."""
        assistant_params = {
            "name": "assistant",
            "assistant_id": str(experiment.assistant.id),
            "citations_enabled": experiment.citations_enabled,
            "input_formatter": experiment.input_formatter or "",
        }

        return self._create_pipeline_with_node(
            experiment=experiment,
            node_type=AssistantNode.__name__,
            node_label="OpenAI Assistant",
            node_params=assistant_params,
        )
