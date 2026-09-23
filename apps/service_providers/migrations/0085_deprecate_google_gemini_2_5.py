from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0084_auto_model_sync_20260919"),
        # Retained so the graph stays stable for environments that already applied this.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() and notify_deprecated_models moved to
        # 0086_auto_model_sync_20260923 so they run only once per deploy.
    ]
