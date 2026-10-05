from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0087_auto_model_sync_20260924"),
        # load_pricing_data() needs the PricingRule table, and this must come after the last
        # migration that changed the seed data
        ("cost_tracking", "0008_rate_update_20260904"),
        # llm_model_migration() repoints evaluators off the custom models it replaces, so the
        # Evaluator FK must be in this migration's app state.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() moved to 0089_deprecate_claude_sonnet_4_5 so it runs only once
        # per deploy.
        load_pricing_data(),
    ]
