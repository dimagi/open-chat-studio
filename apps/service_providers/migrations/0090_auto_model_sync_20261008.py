from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0089_deprecate_claude_sonnet_4_5"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0008_rate_update_20260904"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
    ]
