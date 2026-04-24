from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0048_deprecate_claude_sonnet_opus_4_20250514"),
    ]

    operations = [
        # Mark gpt-4.1-nano (azure + openai) and gpt-3.5-turbo (openai) as deprecated in the DB.
        # These models are being shut down by OpenAI on October 23, 2026.
        # Replacement: gpt-4.1-mini
        llm_model_migration(),
        # Notify affected teams about the deprecation and recommended replacements
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
