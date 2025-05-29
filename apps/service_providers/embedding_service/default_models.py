import dataclasses

from django.db import transaction

from apps.service_providers.models import EmbeddingProviderType


@dataclasses.dataclass
class Model:
    name: str


DEFAULT_EMBEDDING_PROVIDER_MODELS = {
    EmbeddingProviderType.openai: ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"],
}


@transaction.atomic()
def update_embedding_provider_models():
    from apps.service_providers.models import EmbeddingProviderModel

    _update_embedding_provider_models(EmbeddingProviderModel)


def _update_embedding_provider_models(EmbeddingProviderModel):
    """
    This method updates the EmbeddingProviderModel objects in the database to match the
    DEFAULT_EMBEDDING_PROVIDER_MODELS.
    """
    for provider_type, provider_models in DEFAULT_EMBEDDING_PROVIDER_MODELS.items():
        for model in provider_models:
            EmbeddingProviderModel.objects.get_or_create(team=None, name=model, type=provider_type)
