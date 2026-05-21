from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0054_model_fixes_may_2026"),
    ]

    operations = [
        # Add gemini-3.5-flash for both `google` and `google_vertex_ai` providers (1M context)
        llm_model_migration(),
    ]
