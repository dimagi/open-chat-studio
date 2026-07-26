from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0063_alter_embeddingprovidermodel_type_and_more"),
        # The data migration below queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Backfill the new MiniMax default models (MiniMax-M3, MiniMax-M2.7, MiniMax-M2)
        # into existing databases, matching how prior provider/model additions seed.
        # llm_model_migration() moved to 0066_add_gemini_3_6_flash_models
    ]
