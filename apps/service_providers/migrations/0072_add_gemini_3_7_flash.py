from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0071_deprecate_vertex_gemini_2_5"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0001_initial"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() and load_pricing_data() moved to 0078_add_gemini_3_8_flash so they
        # run only once per deploy (the newest migration re-syncs the whole model list and loads
        # the whole seed file, which covers gemini-3.7-flash too).
    ]
