from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0088_auto_model_sync_20260930"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        llm_model_migration(),
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
