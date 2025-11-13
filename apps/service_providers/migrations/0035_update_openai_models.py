from django.db import migrations
import logging


def _update_openai_models(apps, schema_editor):
    from apps.service_providers.llm_service.default_models import _update_llm_provider_models
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
    _update_llm_provider_models(LlmProviderModel)


class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0034_update_openai_models'),
    ]

    operations = [
        migrations.RunPython(_update_openai_models, reverse_code=migrations.RunPython.noop),
    ]
