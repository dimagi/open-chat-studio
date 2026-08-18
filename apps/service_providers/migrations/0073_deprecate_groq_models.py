from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0072_add_gemini_3_7_flash"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        # notify_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Re-sync the whole model list, marking the Groq gemma2-9b-it, llama-3.3-70b-versatile and
        # llama-3.1-8b-instant models deprecated to match Groq's official deprecations.
        llm_model_migration(),
        # Notify affected teams about the deprecations and their recommended replacements.
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
