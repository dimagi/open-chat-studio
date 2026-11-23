import pytest

from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


def _decision_model_ids(provider_type: str, model_name: str) -> dict:
    """The provider and model ids of a provider with a decision model, as a node's params name them."""
    provider = LlmProviderFactory.create(type=provider_type, config={f"{provider_type}_api_key": "test-key"})
    model = LlmProviderModelFactory.create(team=provider.team, type=provider_type, name=model_name)
    return {"llm_provider_id": provider.id, "llm_provider_model_id": model.id}


@pytest.fixture()
def jev_ids(db):
    return _decision_model_ids("jev", "jev-latest")


@pytest.fixture()
def google_decision_ids(db):
    return _decision_model_ids("google", "gemini-decision-1")


@pytest.fixture()
def openai_decision_ids(db):
    return _decision_model_ids("openai", "gpt-6-luna")


@pytest.fixture()
def session(db):
    return ExperimentSessionFactory.create()
