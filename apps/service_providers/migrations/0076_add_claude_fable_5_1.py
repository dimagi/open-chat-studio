from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0075_alter_embeddingprovidermodel_type_and_more"),
        # required so the PricingRule table exists for load_pricing_data()
        ("cost_tracking", "0001_initial"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # Re-sync the whole model list, seeding the Anthropic claude-fable-5-1 model.
        llm_model_migration(),
        # Seed pricing for claude-fable-5-1. load_ai_pricing is idempotent and supersedes on
        # change, so this is safe to leave in place alongside earlier calls.
        load_pricing_data(),
    ]
