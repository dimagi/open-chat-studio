from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0088_auto_model_sync_20260930"),
        # remove_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        # llm_model_migration() and remove_deprecated_models repoint evaluators, so the
        # Evaluator FK must be in this migration's app state.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # Seeds anthropic/claude-sonnet-5-5 and gpt-6.1-sol on openai and azure, and marks
        # anthropic/claude-sonnet-4-5-20250929 as deprecated.
        llm_model_migration(),
        # Remove anthropic/claude-opus-4-20250514, openai/chatgpt-4o-latest, groq/gemma-7b-it
        # and google/gemini-2.0-flash.
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
        # Notify affected teams about the deprecation and the claude-sonnet-4-6 replacement.
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
