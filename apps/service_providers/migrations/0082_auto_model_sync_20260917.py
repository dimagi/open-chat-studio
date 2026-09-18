from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0081_add_deepseek_flash"),
        # the only load_pricing_data() run in the graph, so it must come after the last
        # migration that changed the seed data
        ("cost_tracking", "0008_rate_update_20260904"),
        # llm_model_migration() repoints evaluators off any custom model it replaces, so the
        # Evaluator FK must be in this migration's app state (see _repoint_evaluators).
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        llm_model_migration(),
        load_pricing_data(),
        # Repoint or clear references to the models dropped from DEFAULT_LLM_PROVIDER_MODELS and
        # notify the affected teams.
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
    ]
