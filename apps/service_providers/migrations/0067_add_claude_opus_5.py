from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0066_add_gemini_3_6_flash_models"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0001_initial"),
    ]

    operations = [
        # Add claude-opus-5 for the `anthropic` provider (1M context, 128k max output).
        # llm_model_migration() and load_pricing_data() moved to 0078_add_gemini_3_8_flash so they
        # run only once per deploy (the newest migration re-syncs the whole model list and loads
        # the whole seed file, which covers claude-opus-5 too).
    ]
