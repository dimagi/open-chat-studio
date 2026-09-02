from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0075_alter_embeddingprovidermodel_type_and_more"),
        # required so the PricingRule table exists for load_pricing_data()
        ("cost_tracking", "0001_initial"),
    ]

    operations = [
        # Seed pricing for claude-fable-5-1. load_ai_pricing is idempotent and supersedes on
        # change, so this is safe to leave in place alongside earlier calls.
        load_pricing_data(),
    ]
