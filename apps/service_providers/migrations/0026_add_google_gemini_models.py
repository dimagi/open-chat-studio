import logging
from django.db import migrations, models

logger = logging.getLogger(__name__)

def add_google_gemini_models(apps, schema_editor):
    """Function to update LlmProviderModel with Google Gemini."""
    from apps.service_providers.llm_service.default_models import _update_llm_provider_models
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
    try:
        _update_llm_provider_models(LlmProviderModel)
    except Exception as e:
        logger.error(f"Error updating LlmProviderModel for Google Gemini: {e}")
        raise

class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0025_alter_llmprovider_type_alter_llmprovidermodel_type'),
    ]
