from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0087_auto_model_sync_20260924"),
        ("cost_tracking", "0008_rate_update_20260904"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        load_pricing_data(),
    ]
