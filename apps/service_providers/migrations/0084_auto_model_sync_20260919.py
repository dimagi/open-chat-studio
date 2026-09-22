from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0083_alter_embeddingprovidermodel_type_and_more"),
        # remove_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        ("pipelines", "0027_backfill_node_fks"),
    ]

    operations = [
        # Remove openai/o4-mini-high and groq/whisper-large-v3-turbo
        llm_model_migration(),
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
    ]
