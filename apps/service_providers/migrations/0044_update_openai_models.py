from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0043_migrate_gemini_3_pro_preview"),
    ]

    operations = [
        llm_model_migration(),
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
    ]
