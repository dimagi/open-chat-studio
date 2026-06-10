from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0055_add_gemini_3_5_flash"),
    ]

    operations = [
        # Add claude-opus-4-8 for the `anthropic` provider (1M context)
        # llm_model_migration() moved to 0057_add_claude_fable_5
    ]
