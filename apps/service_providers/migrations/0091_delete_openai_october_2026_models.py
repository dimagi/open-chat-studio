from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0090_auto_model_sync_20261008"),
        ("cost_tracking", "0008_rate_update_20260904"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
    ]
