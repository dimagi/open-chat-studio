from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0065_alter_voiceprovider_type"),
        ("cost_tracking", "0001_initial"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # llm_model_migration() moved to 0067_add_claude_opus_5
        # Seed pricing for the Gemini models added in this migration.
        load_pricing_data(),
    ]
