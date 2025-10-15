import logging
from django.db import migrations, models

logger = logging.getLogger(__name__)

def deprecate_models(apps, schema_editor):
    from apps.service_providers.llm_service.default_models import _update_llm_provider_models
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
    try:
        _update_llm_provider_models(LlmProviderModel)
    except Exception as e:
        logger.error(f"Error updating LlmProviderModel: {e}")
        raise

class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0030_llmprovidermodel_deprecated'),
    ]

    operations = [
        migrations.RunPython(deprecate_models),
    ]
