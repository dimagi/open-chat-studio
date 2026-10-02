from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0076_add_claude_fable_5_1"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # notify_deprecated_models dropped: this release deprecates no new model, and the scan
        # would otherwise re-run on every deploy.
    ]
