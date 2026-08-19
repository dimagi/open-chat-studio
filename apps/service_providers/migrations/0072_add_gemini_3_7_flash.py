from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0071_deprecate_vertex_gemini_2_5"),
        # required so the PricingRule table exists for load_pricing_data()
        ("cost_tracking", "0001_initial"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() moved to 0073_deprecate_groq_models so it runs only once per deploy
        # (the newest migration re-syncs the whole model list, which seeds gemini-3.7-flash too).
        # Seed pricing for gemini-3.7-flash. load_ai_pricing is idempotent, so this stays here.
        load_pricing_data(),
    ]
