import pytest

from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.fixture()
def remote_collection_index(db):
    llm_provider = LlmProviderFactory.create(name="test-provider")
    return CollectionFactory.create(
        name="test-collection",
        llm_provider=llm_provider,
        openai_vector_store_id="vs_123",
        is_index=True,
        is_remote_index=True,
    )
