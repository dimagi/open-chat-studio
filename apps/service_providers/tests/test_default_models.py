from unittest.mock import patch

import pytest

from apps.service_providers.llm_service.default_models import (
    DEFAULT_LLM_PROVIDER_MODELS,
    Model,
    get_default_model,
    update_llm_provider_models,
)
from apps.service_providers.models import LlmProviderModel
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory


def test_all_providers_have_default_models():
    for provider_type in DEFAULT_LLM_PROVIDER_MODELS:
        assert get_default_model(provider_type) is not None


@pytest.mark.django_db()
def test_updates_existing_models():
    candidate = DEFAULT_LLM_PROVIDER_MODELS["openai"][0]
    model, _ = LlmProviderModel.objects.update_or_create(
        team=None, type="openai", name=candidate.name, defaults={"max_token_limit": 50}
    )
    model.save()

    update_llm_provider_models()

    model.refresh_from_db()
    assert model.max_token_limit == candidate.token_limit


@pytest.mark.django_db()
def test_creates_new_models():
    candidate = DEFAULT_LLM_PROVIDER_MODELS["openai"][0]
    try:
        model = LlmProviderModel.objects.get(team=None, type="openai", name=candidate.name)
        model.delete()
    except LlmProviderModel.DoesNotExist:
        pass

    update_llm_provider_models()

    assert LlmProviderModel.objects.filter(team=None, type="openai", name=candidate.name).exists()


@pytest.mark.django_db()
def test_removes_old_models():
    old_global_model = LlmProviderModelFactory(team=None)

    update_llm_provider_models()

    assert not LlmProviderModel.objects.filter(
        team=None, type=old_global_model.type, name=old_global_model.name
    ).exists()


@pytest.mark.django_db()
def test_converts_old_global_models_to_custom_models():
    old_global_model = LlmProviderModelFactory(team=None)
    experiment = ExperimentFactory(llm_provider_model=old_global_model)

    # no custom model should exist
    assert not LlmProviderModel.objects.filter(
        team=experiment.team, type=old_global_model.type, name=old_global_model.name
    ).exists()

    update_llm_provider_models()

    # global model is removed
    assert not LlmProviderModel.objects.filter(
        team=None, type=old_global_model.type, name=old_global_model.name
    ).exists()

    # custom model is created
    custom_model = LlmProviderModel.objects.get(
        team=experiment.team, type=old_global_model.type, name=old_global_model.name
    )
    # experiment is updated to use the custom model
    experiment.refresh_from_db()
    assert experiment.llm_provider_model_id == custom_model.id


@pytest.mark.django_db()
def test_converts_old_global_models_to_custom_models_pipelines():
    old_global_model = LlmProviderModelFactory(team=None)
    pipeline = get_pipeline(old_global_model)

    # no custom model should exist
    assert not LlmProviderModel.objects.filter(
        team=pipeline.team, type=old_global_model.type, name=old_global_model.name
    ).exists()

    update_llm_provider_models()

    # global model is removed
    assert not LlmProviderModel.objects.filter(
        team=None, type=old_global_model.type, name=old_global_model.name
    ).exists()

    # custom model is created
    custom_model = LlmProviderModel.objects.get(
        team=pipeline.team, type=old_global_model.type, name=old_global_model.name
    )
    # pipeline is updated to use the custom model
    pipeline.refresh_from_db()
    assert pipeline.node_set.get(type="LLMResponseWithPrompt").params["llm_provider_model_id"] == custom_model.id
    node_data = [node for node in pipeline.data["nodes"] if node["data"]["type"] == "LLMResponseWithPrompt"]
    assert node_data[0]["data"]["params"]["llm_provider_model_id"] == custom_model.id


@pytest.mark.django_db()
def test_replaces_custom_models_with_global_models():
    experiment = ExperimentFactory()
    old_custom_model = experiment.llm_provider_model
    assert old_custom_model.team is not None  # Ensure it's a custom model

    # no global model should exist
    assert not LlmProviderModel.objects.filter(
        team=None, type=old_custom_model.type, name=old_custom_model.name
    ).exists()

    defaults = {old_custom_model.type: [Model(old_custom_model.name, old_custom_model.max_token_limit)]}
    with patch("apps.service_providers.llm_service.default_models.DEFAULT_LLM_PROVIDER_MODELS", defaults):
        update_llm_provider_models()

    new_global_model = LlmProviderModel.objects.get(team=None, type=old_custom_model.type, name=old_custom_model.name)
    # custom model should now point to the global model
    assert not LlmProviderModel.objects.filter(id=old_custom_model.id).exists()
    experiment.refresh_from_db()
    assert experiment.llm_provider_model_id == new_global_model.id


@pytest.mark.django_db()
def test_converts_custom_models_to_global_models_pipelines():
    custom_model = LlmProviderModelFactory()
    pipeline = get_pipeline(custom_model)

    # no custom model should exist
    assert not LlmProviderModel.objects.filter(team=None, type=custom_model.type, name=custom_model.name).exists()

    defaults = {custom_model.type: [Model(custom_model.name, custom_model.max_token_limit)]}
    with patch("apps.service_providers.llm_service.default_models.DEFAULT_LLM_PROVIDER_MODELS", defaults):
        update_llm_provider_models()

    # custom model is removed
    assert not LlmProviderModel.objects.filter(id=custom_model.id).exists()

    # global model is created
    global_model = LlmProviderModel.objects.get(team=None, type=custom_model.type, name=custom_model.name)
    # pipeline is updated to use the custom model
    pipeline.refresh_from_db()
    assert pipeline.node_set.get(type="LLMResponseWithPrompt").params["llm_provider_model_id"] == global_model.id
    node_data = [node for node in pipeline.data["nodes"] if node["data"]["type"] == "LLMResponseWithPrompt"]
    assert node_data[0]["data"]["params"]["llm_provider_model_id"] == global_model.id


def get_pipeline(llm_provider_model):
    pipeline = PipelineFactory()
    pipeline.data["nodes"].append(
        {
            "id": "1",
            "data": {
                "id": "1",
                "label": "LLM",
                "type": "LLMResponseWithPrompt",
                "params": {
                    "llm_provider_model_id": str(llm_provider_model.id),
                    "prompt": "You are a helpful assistant",
                },
            },
        }
    )
    pipeline.update_nodes_from_data()
    pipeline.save()
    return pipeline
