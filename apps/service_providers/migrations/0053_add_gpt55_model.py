from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):

    dependencies = [
        ("service_providers", "0052_seed_voyage_embedding_models"),
    ]

    operations = [
        # Register gpt-5.5 (OpenAI) with 1,050,000 token context window.
        llm_model_migration(),
    ]
