from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0083_alter_embeddingprovidermodel_type_and_more"),
        # Retained so the graph stays stable for environments that already applied this.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # remove_deprecated_models moved to 0086_auto_model_sync_20260923 so it runs only once
        # per deploy (the newest migration re-syncs the whole model list).
    ]
