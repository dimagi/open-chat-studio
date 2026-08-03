from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0068_traceprovider_metadata"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # Add deepseek-v4-flash for the `deepseek` provider (1M context, 384k max output).
        # llm_model_migration() moved to 0070_deprecate_deepseek_chat_reasoner
    ]
