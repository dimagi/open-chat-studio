from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0058_add_claude_sonnet_5"),
    ]

    operations = [
        # Remove claude-sonnet-4-20250514 for the `anthropic` provider
        # llm_model_migration() and remove_deprecated_models moved to 0061_add_gpt_5_6_models
    ]
