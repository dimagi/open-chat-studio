from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0094_deprecate_openai_gpt_4_models"),
    ]

    operations = [
        # llm_model_migration() moved to 0097_auto_model_sync_20261027
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
