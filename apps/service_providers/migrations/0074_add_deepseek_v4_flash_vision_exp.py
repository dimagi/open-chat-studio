from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0073_deprecate_groq_models"),
        # required so the PricingRule table exists for load_pricing_data()
        ("cost_tracking", "0001_initial"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() moved to 0076_add_claude_fable_5_1 so it runs only once per deploy
        # (the newest migration re-syncs the whole model list, which seeds
        # deepseek-v4-flash-vision-exp too).
        # Seed pricing for deepseek-v4-flash-vision-exp and supersede the stale deepseek-v4-flash
        # and -v4-pro rates with DeepSeek's current card. load_ai_pricing is idempotent and
        # supersedes on change, so this is safe to leave in place alongside earlier calls.
    ]
