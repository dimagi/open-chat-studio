from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):

    dependencies = [
        ("service_providers", "0053_add_gpt55_model"),
    ]

    operations = [
        # OpenAI:
        #   - Fix gpt-5.4 and gpt-5.4-pro context window (k(400)≈409K → 1,050,000)
        #   - Add gpt-5.4-mini (400K context)
        #   - Add gpt-5.4-nano (400K context)
        # Anthropic:
        #   - Fix claude-sonnet-4-6 context window (k(200)≈204K → 1,000,000)
        # Groq:
        #   - Add openai/gpt-oss-120b (131K context)
        #   - Add openai/gpt-oss-20b (131K context)
        llm_model_migration(),
    ]
