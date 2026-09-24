from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0086_auto_model_sync_20260923"),
        # load_pricing_data() needs the PricingRule table, and this must come after the last
        # migration that changed the seed data
        ("cost_tracking", "0008_rate_update_20260904"),
        # llm_model_migration() repoints evaluators off the custom models it replaces, so the
        # Evaluator FK must be in this migration's app state.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # Seeds azure/gpt-6-sol and azure/gpt-6-luna.
        llm_model_migration(),
        load_pricing_data(),
    ]
