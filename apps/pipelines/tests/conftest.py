import pytest

from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.fixture()
def jev_ids(db):
    """The provider and model ids of a Jev provider, as a node's params name them."""
    provider = LlmProviderFactory.create(type="jev", config={"jev_api_key": "test-key"})
    model = LlmProviderModelFactory.create(team=provider.team, type="jev", name="jev-latest")
    return {"llm_provider_id": provider.id, "llm_provider_model_id": model.id}


@pytest.fixture()
def session(db):
    return ExperimentSessionFactory.create()
