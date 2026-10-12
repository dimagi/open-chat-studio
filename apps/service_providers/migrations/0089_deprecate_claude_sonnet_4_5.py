from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0088_auto_model_sync_20260930"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # remove_deprecated_models and notify_deprecated_models moved to
        # 0091_deprecate_google_gemini_3_5_flash so they run only once per deploy.
    ]
