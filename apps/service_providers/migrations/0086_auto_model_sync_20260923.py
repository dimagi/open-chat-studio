from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0085_deprecate_google_gemini_2_5"),
        # load_pricing_data() needs the PricingRule table, and this must come after the last
        # migration that changed the seed data
        ("cost_tracking", "0008_rate_update_20260904"),
        # remove_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        # llm_model_migration() and remove_deprecated_models repoint evaluators off the models
        # they replace or delete, so the Evaluator FK must be in this migration's app state.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        llm_model_migration(),
        # Remove anthropic/claude-opus-4-20250514, openai/chatgpt-4o-latest, groq/gemma-7b-it
        # and google/gemini-2.0-flash.
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
        load_pricing_data(),
    ]
