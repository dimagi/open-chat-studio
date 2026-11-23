from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0105_moderationprovider_updated_at_id_idx"),
        ("cost_tracking", "0008_rate_update_20260904"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
    ]
