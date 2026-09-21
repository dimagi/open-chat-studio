from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0080_add_gemini_3_8_flash"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0008_rate_update_20260904"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() and load_pricing_data() moved to 0082_auto_model_sync_20260917 so
        # they run only once per deploy (the newest migration re-syncs the whole model list).
    ]
