from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0085_deprecate_google_gemini_2_5"),
        ("cost_tracking", "0008_rate_update_20260904"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = []
