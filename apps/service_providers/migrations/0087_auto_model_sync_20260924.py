from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0086_auto_model_sync_20260923"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0008_rate_update_20260904"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() and load_pricing_data() moved to
        # 0088_auto_model_sync_20260930 so they run only once per deploy.
    ]
