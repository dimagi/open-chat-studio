from collections import defaultdict

from django.db.models import Q

from apps.assistants.models import OpenAiAssistant
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import Experiment
from apps.ocs_notifications.notifications import deprecated_model_notification
from apps.service_providers.llm_service.default_models import DEFAULT_LLM_PROVIDER_MODELS
from apps.service_providers.models import LlmProviderModel
from apps.teams.models import Team
from apps.utils.deletion import get_related_pipelines_queryset


class Command(IdempotentCommand):
    help = "Notify teams about deprecated LLM models and recommended replacements"
    migration_name = "notify_deprecated_models"
    disable_audit = True

    def perform_migration(self, dry_run=False):
        # Find all deprecated models (with or without a replacement)
        deprecated_with_replacement = {}
        for provider_type, provider_models in DEFAULT_LLM_PROVIDER_MODELS.items():
            for model in provider_models:
                if model.deprecated:
                    deprecated_with_replacement[(provider_type, model.name)] = model.replacement or None

        if not deprecated_with_replacement:
            self.stdout.write(self.style.SUCCESS("No deprecated models found"))
            return

        # Find DB records for each deprecated model
        db_models = []  # list of (db_model, model_name, replacement_name)
        for (provider_type, model_name), replacement in deprecated_with_replacement.items():
            try:
                db_model = LlmProviderModel.objects.get(team=None, type=provider_type, name=model_name)
                db_models.append((db_model, model_name, replacement))
            except LlmProviderModel.DoesNotExist:
                if self.verbosity > 1:
                    self.stdout.write(f"  Model not in DB: {provider_type}/{model_name}")

        if not db_models:
            self.stdout.write(self.style.SUCCESS("No deprecated models found in database"))
            return

        # For each deprecated model, find affected teams
        # Structure: {db_model_id: (teams_data, model_name, replacement)}
        # teams_data: {team_id: {"chatbots": set, "pipelines": set, "assistants": set}}
        affected_by_model = {}

        for db_model, _model_name, replacement in db_models:
            teams_data = defaultdict(lambda: {"chatbots": {}, "pipelines": {}, "assistants": {}})

            related_pipeline_nodes = get_related_pipelines_queryset(db_model, "llm_provider_model_id")
            nodes_by_pipeline = defaultdict(list)
            pipelines = []
            for node in related_pipeline_nodes.select_related("pipeline").all():
                pipelines.append(node.pipeline)
                nodes_by_pipeline[node.pipeline_id].append(node)

            referenced_experiments = Experiment.objects.filter(pipeline_id__in=list(nodes_by_pipeline)).filter(
                Q(working_version__isnull=True) | Q(is_default_version=True)
            )
            referenced_pipeline_ids = {exp.pipeline_id for exp in referenced_experiments}
            unreferenced_pipelines = [p for p in pipelines if p.id not in referenced_pipeline_ids]

            referenced_assistants = OpenAiAssistant.objects.filter(
                llm_provider_model=db_model, working_version__isnull=True
            )

            for exp in referenced_experiments:
                teams_data[exp.team_id]["chatbots"][exp.name] = exp.get_absolute_url()
            for pipeline in unreferenced_pipelines:
                teams_data[pipeline.team_id]["pipelines"][pipeline.name] = pipeline.get_absolute_url()
            for assistant in referenced_assistants:
                teams_data[assistant.team_id]["assistants"][assistant.name] = assistant.get_absolute_url()

            affected_by_model[db_model.id] = (teams_data, f"{db_model.type}/{db_model.name}", replacement)

        total_affected = sum(len(td) for td, _, _ in affected_by_model.values())
        self.stdout.write(f"Found {len(db_models)} deprecated models affecting {total_affected} team(s)")

        if self.verbosity > 1:
            all_team_ids = {tid for td, _, _ in affected_by_model.values() for tid in td}
            teams = {t.id: t for t in Team.objects.filter(id__in=all_team_ids)}
            for _db_model_id, (teams_data, model_name, replacement) in affected_by_model.items():
                self.stdout.write(f"\n  Model: {model_name} → {replacement}")
                for team_id, data in teams_data.items():
                    self.stdout.write(f"    Team: {teams[team_id].name}")
                    self.stdout.write(f"      Chatbots: {sorted(data['chatbots'])}")
                    self.stdout.write(f"      Pipelines: {sorted(data['pipelines'])}")
                    self.stdout.write(f"      Assistants: {sorted(data['assistants'])}")

        if dry_run:
            return f"Would notify {total_affected} team(s)"

        # Send notifications: once per deprecated model per affected team
        all_team_ids = {tid for td, _, _ in affected_by_model.values() for tid in td}
        teams_objs = {t.id: t for t in Team.objects.filter(id__in=all_team_ids)}

        total_notified = 0
        for _db_model_id, (teams_data, model_name, replacement) in affected_by_model.items():
            for team_id, data in teams_data.items():
                deprecated_model_notification(
                    team=teams_objs[team_id],
                    model_name=model_name,
                    replacement_model_name=replacement,
                    affected_chatbots=data["chatbots"],
                    affected_pipelines=data["pipelines"],
                    affected_assistants=data["assistants"],
                )
                total_notified += 1

        self.stdout.write(self.style.SUCCESS(f"Notified {total_notified} team(s)"))
        return f"Notified {total_notified} team(s)"
