from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0047_add_claude_opus_4_7"),
    ]

    operations = [
        # Mark claude-sonnet-4-20250514 and claude-opus-4-20250514 as deprecated in the DB
        llm_model_migration(),
        # Notify affected teams about the deprecation and recommended replacements
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
