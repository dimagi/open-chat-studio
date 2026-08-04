from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0070_deepseek_model_updates"),
        ("cost_tracking", "0001_initial"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        # notify_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Add gemini-3.1-flash-lite and mark the google_vertex_ai gemini-2.5 models deprecated
        # ahead of Google's 2026-10-20 Extended Lifecycle Access date.
        llm_model_migration(),
        # Seed pricing for gemini-3.1-flash-lite.
        load_pricing_data(),
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
