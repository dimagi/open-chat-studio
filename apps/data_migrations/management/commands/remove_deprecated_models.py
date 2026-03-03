from collections import defaultdict

from django.db.models import Q

from apps.assistants.models import OpenAiAssistant
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import Experiment
from apps.ocs_notifications.notifications import deleted_model_notification
from apps.service_providers.llm_service.default_models import DELETED_MODELS, _update_pipeline_node_param
from apps.service_providers.models import LlmProviderModel
from apps.teams.models import Team
from apps.utils.deletion import get_related_objects, get_related_pipelines_queryset


def _parse_deleted_models():
    """Yield (provider_type, model_name, replacement_name_or_None) for each entry in DELETED_MODELS."""
    for entry in DELETED_MODELS:
        if len(entry) == 2:
            yield entry[0], entry[1], None
        elif len(entry) == 3:
            yield entry[0], entry[1], entry[2]
        else:
            raise ValueError(
                f"Invalid DELETED_MODELS entry {entry!r}. "
                "Expected (provider, name) or (provider, name, replacement_name)."
            )


class Command(IdempotentCommand):
    help = "Remove deprecated LLM models and notify affected teams"
    migration_name = "remove_deprecated_models"
    disable_audit = True

    def perform_migration(self, dry_run=False):
        # Resolve DB records and replacement models
        # Each entry: (LlmProviderModel, replacement_name_or_None, replacement_LlmProviderModel_or_None)
        models_to_delete = []
        for provider_type, model_name, replacement_name in _parse_deleted_models():
            try:
                db_model = LlmProviderModel.objects.get(team=None, type=provider_type, name=model_name)
            except LlmProviderModel.DoesNotExist:
                if self.verbosity > 1:
                    self.stdout.write(f"  Model not found (already deleted): {provider_type}/{model_name}")
                continue

            replacement_model = None
            if replacement_name:
                try:
                    replacement_model = LlmProviderModel.objects.get(
                        team=None, type=provider_type, name=replacement_name
                    )
                except LlmProviderModel.DoesNotExist:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Replacement model '{provider_type}/{replacement_name}' not found in DB; "
                            "references will be cleared instead."
                        )
                    )

            models_to_delete.append((db_model, replacement_name, replacement_model))

        if not models_to_delete:
            self.stdout.write(self.style.SUCCESS("No deleted models found"))
            return

        # Build affected resources per model per team
        affected_by_model = {}  # {db_model.id: {team_id: {"chatbots": set, "pipelines": set, "assistants": set}}}

        for db_model, _replacement_name, _replacement_model in models_to_delete:
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

            affected_by_model[db_model.id] = teams_data

        total_teams = len({tid for td in affected_by_model.values() for tid in td})
        self.stdout.write(f"Found {len(models_to_delete)} deleted models affecting {total_teams} teams")

        if self.verbosity > 1:
            all_team_ids = {tid for td in affected_by_model.values() for tid in td}
            teams = {t.id: t for t in Team.objects.filter(id__in=all_team_ids)}
            for db_model, replacement_name, _ in models_to_delete:
                self.stdout.write(f"\n  Model: {db_model.type}/{db_model.name} → {replacement_name or '(clear)'}")
                for team_id, data in affected_by_model[db_model.id].items():
                    team = teams[team_id]
                    self.stdout.write(f"    Team: {team.name}")
                    self.stdout.write(f"      Chatbots: {sorted(data['chatbots'])}")
                    self.stdout.write(f"      Pipelines: {sorted(data['pipelines'])}")
                    self.stdout.write(f"      Assistants: {sorted(data['assistants'])}")

        if dry_run:
            return f"Would remove {len(models_to_delete)} models"

        all_team_ids = {tid for td in affected_by_model.values() for tid in td}
        teams_objs = {t.id: t for t in Team.objects.filter(id__in=all_team_ids)}

        # Delete each model and notify after successful deletion
        total_deleted = 0
        for db_model, replacement_name, replacement_model in models_to_delete:
            db_model_id = db_model.id  # Capture before delete sets pk to None

            # Update FK references (assistants, analyses, etc.) to replacement, or let cascade handle them
            if replacement_model:
                for obj in get_related_objects(db_model):
                    fields_to_update = [
                        f
                        for f in obj._meta.fields
                        if f.related_model == LlmProviderModel and getattr(obj, f.attname) == db_model.id
                    ]
                    for field in fields_to_update:
                        setattr(obj, field.attname, replacement_model.id)
                    if fields_to_update:
                        obj.save(update_fields=[f.name for f in fields_to_update])

            # Update pipeline node references (stored as JSON params, not DB FKs)
            related_pipeline_nodes = get_related_pipelines_queryset(db_model, "llm_provider_model_id")
            new_value = replacement_model.id if replacement_model else None
            for node in related_pipeline_nodes.select_related("pipeline").all():
                _update_pipeline_node_param(node.pipeline, node, "llm_provider_model_id", new_value)

            # Delete the model (bypass custom delete to avoid related-object pre-checks)
            super(LlmProviderModel, db_model).delete()
            total_deleted += 1

            # Notify after successful deletion
            for team_id, data in affected_by_model[db_model_id].items():
                deleted_model_notification(
                    team=teams_objs[team_id],
                    model_name=f"{db_model.type}/{db_model.name}",
                    replacement_model_name=replacement_name if replacement_model else None,
                    affected_chatbots=data["chatbots"],
                    affected_pipelines=data["pipelines"],
                    affected_assistants=data["assistants"],
                )

        self.stdout.write(self.style.SUCCESS(f"Removed {total_deleted} models"))
        return f"Removed {total_deleted} models, notified {total_teams} teams"
