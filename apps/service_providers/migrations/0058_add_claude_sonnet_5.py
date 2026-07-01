from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0057_add_claude_fable_5"),
    ]

    operations = [
        # Add claude-sonnet-5 for the `anthropic` provider (1M context)
        llm_model_migration(),
    ]
