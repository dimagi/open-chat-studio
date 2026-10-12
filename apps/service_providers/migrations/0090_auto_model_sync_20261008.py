from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0089_deprecate_claude_sonnet_4_5"),
        ("cost_tracking", "0008_rate_update_20260904"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        llm_model_migration(),
        load_pricing_data(),
    ]
