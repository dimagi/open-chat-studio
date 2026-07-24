from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0060_authprovider__auth_data_alter_authprovider_type"),
        # remove_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Add gpt-5.6-terra, gpt-5.6-sol and gpt-5.6-luna for the `openai` provider (1.1M context)
        # llm_model_migration() and remove_deprecated_models moved to 0066_add_gemini_3_6_flash_models
    ]
