from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0076_add_claude_fable_5_1"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() and notify_deprecated_models moved to 0078_add_gemini_3_8_flash
        # so they run only once per deploy.
    ]
