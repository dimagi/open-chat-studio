from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0083_alter_embeddingprovidermodel_type_and_more"),
        # Retained so the graph stays stable for environments that already applied this.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() and remove_deprecated_models moved to
        # 0085_deprecate_google_gemini_2_5 so they run only once per deploy (the newest
        # migration re-syncs the whole model list and re-scans deletions).
    ]
