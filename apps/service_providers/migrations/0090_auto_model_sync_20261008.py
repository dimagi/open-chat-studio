from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0089_deprecate_claude_sonnet_4_5"),
        ("cost_tracking", "0008_rate_update_20260904"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() moved to 0091_deprecate_google_gemini_3_5_flash so it runs
        # only once per deploy.
        load_pricing_data(),
    ]
