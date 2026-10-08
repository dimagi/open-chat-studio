from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0089_deprecate_claude_sonnet_4_5"),
        ("cost_tracking", "0008_rate_update_20260904"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        llm_model_migration(),
        load_pricing_data(),
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
