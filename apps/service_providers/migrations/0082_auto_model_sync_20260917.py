from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0081_add_deepseek_flash"),
        # the only load_pricing_data() run in the graph, so it must come after the last
        # migration that changed the seed data
        ("cost_tracking", "0008_rate_update_20260904"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        load_pricing_data(),
        # llm_model_migration() and remove_deprecated_models moved to 0083_auto_model_sync_20260919
        # so they run only once per deploy (the newest migration re-syncs the whole model list).
    ]
