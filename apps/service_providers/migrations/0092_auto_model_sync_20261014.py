from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0091_delete_openai_october_2026_models"),
        ("cost_tracking", "0008_rate_update_20260904"),
    ]

    operations = [
        # llm_model_migration() moved to 0094_auto_model_sync_20261018 so it runs only once per deploy.
        load_pricing_data(),
    ]
