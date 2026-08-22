from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0073_deprecate_groq_models"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        # notify_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Re-sync the whole model list, seeding the DeepSeek deepseek-v4-flash-vision-exp model.
        llm_model_migration(),
        # Moved from 0073_deprecate_groq_models so the Groq deprecations are still announced
        # exactly once per deploy. notify_deprecated_models is once-per-team-per-model, so
        # re-scanning here does not re-notify teams that already heard about a model.
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
