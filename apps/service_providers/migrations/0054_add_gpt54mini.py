from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):

    dependencies = [
        ("service_providers", "0053_add_gpt55_model"),
    ]

    operations = [
        # Register gpt-5.4-mini (OpenAI) with 400K token context window.
        llm_model_migration(),
    ]
