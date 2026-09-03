from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0076_add_claude_fable_5_1"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # Re-sync the whole model list, marking gpt-5/-mini/-nano/-pro deprecated in the DB.
        llm_model_migration(),
        # Notify affected teams about the deprecation and recommended replacements.
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
