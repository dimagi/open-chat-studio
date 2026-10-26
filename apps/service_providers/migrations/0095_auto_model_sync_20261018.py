from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0094_deprecate_openai_gpt_4_models"),
    ]

    operations = [
        llm_model_migration(),
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
