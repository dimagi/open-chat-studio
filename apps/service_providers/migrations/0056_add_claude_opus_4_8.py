from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0055_add_gemini_3_5_flash"),
    ]

    operations = [
        # Add claude-opus-4-8 (1M context, ClaudeOpus47Parameters)
        llm_model_migration(),
    ]
