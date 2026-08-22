from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0072_add_gemini_3_7_flash"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        # Retained so the graph stays stable for environments that already applied this.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # llm_model_migration() and notify_deprecated_models moved to
        # 0074_add_deepseek_v4_flash_vision_exp so they run only once per deploy (the newest
        # migration re-syncs the whole model list, which still marks the Groq gemma2-9b-it,
        # llama-3.3-70b-versatile and llama-3.1-8b-instant models deprecated, and re-scans
        # deprecations to notify affected teams).
    ]
