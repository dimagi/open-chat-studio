from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0100_auto_model_sync_20261110"),
        ("cost_tracking", "0008_rate_update_20260904"),
    ]

    operations = [
        load_pricing_data(),
    ]
