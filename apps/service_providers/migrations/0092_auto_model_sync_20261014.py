from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0091_delete_openai_october_2026_models"),
        ("cost_tracking", "0008_rate_update_20260904"),
    ]

    operations = [
        load_pricing_data(),
    ]
