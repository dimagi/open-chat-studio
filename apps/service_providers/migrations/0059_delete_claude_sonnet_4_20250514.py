from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0058_add_claude_sonnet_5"),
        # The data migration below queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Remove claude-sonnet-4-20250514 for the `anthropic` provider
        llm_model_migration(),
        # Migrate references to claude-sonnet-4-20250514 to claude-sonnet-4-6 and notify affected teams
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
    ]
