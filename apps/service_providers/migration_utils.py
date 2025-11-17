from django.db import migrations


def llm_model_migration():
    def _update_llm_models(apps, schema_editor):
        from apps.service_providers.llm_service.default_models import _update_llm_provider_models

        LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
        _update_llm_provider_models(LlmProviderModel)

    return migrations.RunPython(_update_llm_models, migrations.RunPython.noop)


def embedding_model_migration():
    def update_embedding_models(apps, schema_editor):
        from apps.service_providers.llm_service.default_models import _update_embedding_provider_models

        EmbeddingProviderModel = apps.get_model("service_providers", "EmbeddingProviderModel")
        _update_embedding_provider_models(EmbeddingProviderModel)

    return migrations.RunPython(update_embedding_models, migrations.RunPython.noop)
