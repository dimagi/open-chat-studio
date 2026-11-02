from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0096_moderationprovider"),
        ("cost_tracking", "0008_rate_update_20260904"),
    ]

    operations = [
        # llm_model_migration() moved to 0098_auto_model_sync_20261029
        load_pricing_data(),
    ]
