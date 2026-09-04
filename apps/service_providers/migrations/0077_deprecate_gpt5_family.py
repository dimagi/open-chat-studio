from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0076_add_claude_fable_5_1"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() moved to 0078_add_gemini_3_8_flash so it runs only once per deploy
        # (the newest migration re-syncs the whole model list, which marks gpt-5/-mini/-nano/-pro
        # deprecated too). The notification reads the deprecation flags from
        # DEFAULT_LLM_PROVIDER_MODELS rather than the DB, so it does not depend on that re-sync
        # having run first.
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
