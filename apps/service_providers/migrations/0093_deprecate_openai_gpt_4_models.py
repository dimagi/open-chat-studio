from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0092_auto_model_sync_20261014"),
    ]

    operations = [
        llm_model_migration(),
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
