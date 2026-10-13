from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0088_auto_model_sync_20260930"),
        # Retained so the graph stays stable for environments that already applied this.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # remove_deprecated_models and notify_deprecated_models moved to
        # 0091_delete_openai_october_2026_models so they run only once per deploy.
    ]
