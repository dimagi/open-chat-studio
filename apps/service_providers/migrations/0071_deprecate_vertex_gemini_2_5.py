from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0070_deepseek_model_updates"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0001_initial"),
        # notify_deprecated_models reads Evaluator through LlmProviderModel.evaluators, so the
        # Evaluator FK must be in this migration's app state.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        # notify_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # llm_model_migration() and load_pricing_data() moved to 0072_add_gemini_3_7_flash so they
        # run only once per deploy. This notification stays here because the deprecations it
        # announces are this migration's: the deprecated flags come from
        # DEFAULT_LLM_PROVIDER_MODELS and the google_vertex_ai gemini-2.5 rows it looks up were
        # seeded by earlier migrations, so it does not depend on the moved llm_model_migration().
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
