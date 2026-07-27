from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0066_add_gemini_3_6_flash_models"),
        ("cost_tracking", "0001_initial"),
    ]

    operations = [
        # Add claude-opus-5 for the `anthropic` provider (1M context, 128k max output).
        llm_model_migration(),
        # Seed pricing for claude-opus-5 ($5 / $25 per MTok).
        load_pricing_data(),
    ]
