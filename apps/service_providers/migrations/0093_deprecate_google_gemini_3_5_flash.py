from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0092_auto_model_sync_20261014"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration(), remove_deprecated_models and notify_deprecated_models moved to
        # 0094_auto_model_sync_20261018 so they run only once per deploy.
    ]
