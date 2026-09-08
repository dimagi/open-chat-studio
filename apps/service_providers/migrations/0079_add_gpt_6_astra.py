from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0078_llmprovider_extra_data"),
        ("cost_tracking", "0008_rate_update_20260904"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = []
