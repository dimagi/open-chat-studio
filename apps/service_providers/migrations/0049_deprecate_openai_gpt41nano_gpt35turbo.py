from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0048_deprecate_claude_sonnet_opus_4_20250514"),
    ]

    operations = [
        # Notify affected teams about the deprecation and recommended replacements
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
