from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0076_add_claude_fable_5_1"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # Notify affected teams about the deprecation and recommended replacements.
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
