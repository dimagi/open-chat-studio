from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0093_deprecate_google_gemini_3_5_flash"),
    ]

    operations = [
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
