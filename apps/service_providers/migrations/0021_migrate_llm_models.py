import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def _update_default_models(apps, schema_editor):
    from apps.service_providers.llm_service.default_models import _update_llm_provider_models
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
    _update_llm_provider_models(LlmProviderModel)


def _update_groq_perplexity(apps, schema_editor):
    from apps.service_providers.llm_service.default_models import DEFAULT_LLM_PROVIDER_MODELS
    LlmProvider = apps.get_model("service_providers", "LlmProvider")
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")

    for provider in LlmProvider.objects.filter(type="openai"):
        api_base = provider.config.get("openai_api_base")
        if api_base == "https://api.groq.com/openai/v1/":
            provider.type = "groq"
            provider.save(update_fields=["type"])
        elif api_base == "https://api.perplexity.ai/":
            provider.type = "perplexity"
            provider.save(update_fields=["type"])

    for provider_type, provider_models in DEFAULT_LLM_PROVIDER_MODELS.items():
        if provider_type not in ("groq", "perplexity"):
            continue
        for model in provider_models:
            LlmProviderModel.objects.filter(team__isnull=False, type="openai", name=model.name).update(
                type=provider_type
            )


class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0020_auto_20241111_1113'),
    ]
