from collections import defaultdict

from django.db.models import Q

from apps.assistants.models import OpenAiAssistant
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import Experiment
from apps.service_providers.llm_service.default_models import DELETED_MODELS, _update_pipeline_node_param
from apps.service_providers.models import LlmProviderModel
from apps.teams.email import collect_team_admin_emails, send_bulk_team_admin_emails
from apps.teams.models import Team
from apps.utils.deletion import get_related_pipelines_queryset


class Command(IdempotentCommand):
    help = "Remove deprecated LLM models and notify team admins"
    migration_name = "remove_deprecated_models"
    disable_audit = True

    def perform_migration(self, dry_run=False):
        # Find all models to delete
        models_to_delete = []
        for provider_type, model_name in DELETED_MODELS:
            try:
                model = LlmProviderModel.objects.get(team=None, type=provider_type, name=model_name)
                models_to_delete.append(model)
            except LlmProviderModel.DoesNotExist:
                if self.verbosity > 1:
                    self.stdout.write(f"  Model not found (already deleted): {provider_type}/{model_name}")

        if not models_to_delete:
            self.stdout.write(self.style.SUCCESS("No deleted models found"))
            return

        # Build affected objects by team (only working / published versions for email notifications)
        teams_data = defaultdict(lambda: {"chatbots": {}, "models": set(), "pipelines": {}, "assistants": set()})

        for model in models_to_delete:
            related_pipeline_nodes = get_related_pipelines_queryset(model, "llm_provider_model_id")
            nodes_by_pipeline = defaultdict(list)
            pipelines = []
            for node in related_pipeline_nodes.select_related("pipeline").all():
                pipelines.append(node.pipeline)
                nodes_by_pipeline[node.pipeline_id].append(node.name or node.flow_id)

            referenced_experiments = Experiment.objects.filter(pipeline_id__in=list(nodes_by_pipeline)).filter(
                Q(working_version__isnull=True) | Q(is_default_version=True)
            )
            referenced_pipelines = {experiment.pipeline_id for experiment in referenced_experiments}
            unreferenced_pipelines = [pipeline for pipeline in pipelines if pipeline.id not in referenced_pipelines]
            referenced_assistants = OpenAiAssistant.objects.filter(
                llm_provider_model=model, working_version__isnull=True
            )

            for exp in referenced_experiments:
                if exp.name not in teams_data[exp.team_id]["chatbots"]:
                    teams_data[exp.team_id]["chatbots"][exp.name] = []
                if exp.pipeline_id:
                    teams_data[exp.team_id]["chatbots"][exp.name].extend(nodes_by_pipeline[exp.pipeline_id])
                teams_data[exp.team_id]["models"].add(f"{model.type}/{model.name}")

            for pipeline in unreferenced_pipelines:
                if pipeline.name not in teams_data[pipeline.team_id]["pipelines"]:
                    teams_data[pipeline.team_id]["pipelines"][pipeline.name] = []
                teams_data[pipeline.team_id]["pipelines"][pipeline.name].extend(nodes_by_pipeline[pipeline.id])
                teams_data[pipeline.team_id]["models"].add(f"{model.type}/{model.name}")

            for assistant in referenced_assistants:
                teams_data[assistant.team_id]["assistants"].add(assistant.name)
                teams_data[assistant.team_id]["models"].add(f"{model.type}/{model.name}")

        # Convert to email context format
        teams_context = {}
        for team_id, data in teams_data.items():
            # Build list of chatbot details with their affected nodes
            chatbots = []
            for name in sorted(data["chatbots"].keys()):
                nodes = list(set(data["chatbots"][name]))  # Deduplicate nodes
                chatbots.append({"name": name, "nodes": sorted(nodes) if nodes else []})

            # Build list of pipeline details with their affected nodes
            pipelines = []
            for name in sorted(data["pipelines"].keys()):
                nodes = list(set(data["pipelines"][name]))  # Deduplicate nodes
                pipelines.append({"name": name, "nodes": sorted(nodes)})

            assistants = sorted(data["assistants"])
            models = sorted(data["models"])

            teams_context[team_id] = {
                "chatbots": chatbots,
                "chatbot_count": len(chatbots),
                "pipelines": pipelines,
                "pipeline_count": len(pipelines),
                "assistant_names": assistants,
                "assistant_count": len(assistants),
                "model_names": models,
                "model_count": len(models),
            }

        # Show summary
        total_models = len(models_to_delete)
        total_teams = len(teams_context)

        if self.verbosity > 1:
            # Verbose output: show details for each team
            teams = {t.id: t for t in Team.objects.filter(id__in=teams_context.keys())}

            self.stdout.write(f"\nFound {total_models} deprecated models affecting {total_teams} teams:")
            for team_id, context in teams_context.items():
                team = teams[team_id]
                admin_emails = collect_team_admin_emails(team)
                self.stdout.write(f"\n  Team: {team.name} (slug: {team.slug})")
                self.stdout.write(f"    Affected models ({context['model_count']}):")
                for model_name in context["model_names"]:
                    self.stdout.write(f"      - {model_name}")
                self.stdout.write(f"    Affected chatbots ({context['chatbot_count']}):")
                for chatbot in context["chatbots"]:
                    if chatbot["nodes"]:
                        nodes_str = ", ".join(chatbot["nodes"])
                        self.stdout.write(f"      - {chatbot['name']} (nodes: {nodes_str})")
                    else:
                        self.stdout.write(f"      - {chatbot['name']}")
                self.stdout.write(f"    Affected pipelines ({context['pipeline_count']}):")
                for pipeline in context["pipelines"]:
                    nodes_str = ", ".join(pipeline["nodes"])
                    self.stdout.write(f"      - {pipeline['name']} (nodes: {nodes_str})")
                self.stdout.write(f"    Affected assistants ({context['assistant_count']}):")
                for name in context["assistant_names"]:
                    self.stdout.write(f"      - {name}")
                self.stdout.write(f"    Will notify {len(admin_emails)} admin(s): {', '.join(admin_emails)}")
        else:
            # Normal output: just summary
            self.stdout.write(f"Found {total_models} deprecated models affecting {total_teams} teams")

        if dry_run:
            return f"Would remove {total_models} deprecated models"

        # Send emails to team admins
        results = {}
        if teams_context:
            results = send_bulk_team_admin_emails(
                teams_context=teams_context,
                subject_template="Open Chat Studio: Deprecated models removed from {{ team.name }}",
                body_template_path="service_providers/email/deprecated_models_removal.txt",
                fail_silently=False,
            )

            # Report email results
            if self.verbosity > 1:
                self.stdout.write("\nEmail results:")
                self.stdout.write(f"  Sent: {results['sent']}")
                self.stdout.write(f"  No admins: {results['no_admins']}")
                self.stdout.write(f"  Failed: {results['failed']}")
                for error in results["errors"]:
                    self.stdout.write(self.style.ERROR(f"  Error: {error}"))
            else:
                self.stdout.write(f"Sent {results['sent']} email(s)")
                if results["failed"] > 0:
                    self.stdout.write(self.style.WARNING(f"{results['failed']} email(s) failed"))
                if results["errors"]:
                    for error in results["errors"]:
                        self.stdout.write(self.style.ERROR(f"  {error}"))

        # Delete models and update references
        total_deleted = 0
        for model in models_to_delete:
            # Update pipeline node references
            related_pipeline_nodes = get_related_pipelines_queryset(model, "llm_provider_model_id")
            for node in related_pipeline_nodes.select_related("pipeline").all():
                _update_pipeline_node_param(node.pipeline, node, "llm_provider_model_id", None)

            # Delete the model
            # Bypass related model checks in LlmProviderModel.delete
            super(LlmProviderModel, model).delete()
            total_deleted += 1

        self.stdout.write(self.style.SUCCESS(f"Removed {total_deleted} models"))
        return f"Removed {total_deleted} models, notified {results.get('sent', 0)} teams"
