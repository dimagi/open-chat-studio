from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0057_add_claude_fable_5"),
    ]

    operations = [
        # Add claude-sonnet-5 for the `anthropic` provider (1M context)
        # llm_model_migration() moved to 0059_delete_claude_sonnet_4_20250514
    ]
