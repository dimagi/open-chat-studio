from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0071_deprecate_vertex_gemini_2_5"),
        ("cost_tracking", "0001_initial"),
        # Retained so the graph stays stable for environments that already applied this.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = []
