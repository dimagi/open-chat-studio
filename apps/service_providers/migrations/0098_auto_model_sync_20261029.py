from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0097_auto_model_sync_20261027"),
        ("cost_tracking", "0008_rate_update_20260904"),
    ]

    operations = [
        llm_model_migration(),
        load_pricing_data(),
    ]
