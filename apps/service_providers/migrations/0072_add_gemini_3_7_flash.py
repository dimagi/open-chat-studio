from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0071_deprecate_vertex_gemini_2_5"),
        ("cost_tracking", "0001_initial"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        # notify_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Add gemini-3.7-flash for the google and google_vertex_ai providers.
        llm_model_migration(),
        # Seed pricing for gemini-3.7-flash.
        load_pricing_data(),
        # Carried over from 0071: announce the google_vertex_ai gemini-2.5 deprecations to any
        # environment that had not applied 0071 before its operations moved here. Notifications
        # are once per team per model, so rerunning does not re-notify.
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
