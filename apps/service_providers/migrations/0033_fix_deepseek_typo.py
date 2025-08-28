from django.db import migrations
import logging


def update_llm_models(apps, schema_editor):
    from apps.service_providers.llm_service.default_models import _update_llm_provider_models
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
    try:
        _update_llm_provider_models(LlmProviderModel)
    except Exception as e:
        logging.error(f"Error updating EmbeddingProviderModel: {e}")
        raise


class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0032_add_google_embedding_model_providers'),
    ]

    operations = [
        migrations.RunPython(update_llm_models, reverse_code=migrations.RunPython.noop),
    ]
