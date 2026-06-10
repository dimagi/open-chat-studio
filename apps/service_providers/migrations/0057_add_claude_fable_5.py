from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0056_add_claude_opus_4_8"),
    ]

    operations = [
        # Add claude-fable-5 for the `anthropic` provider (1M context)
        llm_model_migration(),
    ]
