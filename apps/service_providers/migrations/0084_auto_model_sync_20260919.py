from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0083_alter_embeddingprovidermodel_type_and_more"),
        # remove_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        # remove_deprecated_models repoints evaluators off each model it deletes, so the
        # Evaluator FK must be in this migration's app state.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        ("pipelines", "0027_backfill_node_fks"),
    ]

    operations = [
        # Remove openai/o4-mini-high and groq/whisper-large-v3-turbo.
        # llm_model_migration() moved to 0085_deprecate_google_gemini_2_5 so it runs only
        # once per deploy.
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
    ]
