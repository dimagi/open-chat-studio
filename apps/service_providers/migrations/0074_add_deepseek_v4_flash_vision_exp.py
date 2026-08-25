from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0073_deprecate_groq_models"),
        # required so the PricingRule table exists for load_pricing_data()
        ("cost_tracking", "0001_initial"),
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
        # Seed pricing for deepseek-v4-flash-vision-exp and supersede the stale deepseek-v4-flash
        # and -v4-pro rates with DeepSeek's current card. load_ai_pricing is idempotent and
        # supersedes on change, so this is safe to leave in place alongside earlier calls.
        load_pricing_data(),
    ]
