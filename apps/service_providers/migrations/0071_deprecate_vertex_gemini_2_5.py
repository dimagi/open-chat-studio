from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0070_deepseek_model_updates"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0001_initial"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Moved to 0072_add_gemini_3_7_flash, which reruns them.
    ]
