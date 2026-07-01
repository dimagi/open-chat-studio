from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0056_add_claude_opus_4_8"),
    ]

    operations = [
        # Add claude-fable-5 for the `anthropic` provider (1M context)
        # llm_model_migration() moved to 0058_add_claude_sonnet_5
    ]
