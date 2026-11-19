from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0101_auto_model_sync_20261111"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = []
