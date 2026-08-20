from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0070_deepseek_model_updates"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0001_initial"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        # Retained so the graph stays stable for environments that already applied this.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # llm_model_migration() and load_pricing_data() moved to 0072_add_gemini_3_7_flash and
        # notify_deprecated_models moved to 0073_deprecate_groq_models so they run only once per
        # deploy (the newest migration re-syncs the whole model list and re-scans deprecations).
    ]
