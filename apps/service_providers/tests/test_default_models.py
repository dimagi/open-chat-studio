from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.pipelines.tests.utils import content_flow_node
from apps.service_providers.llm_service.default_models import (
    DEFAULT_EMBEDDING_PROVIDER_MODELS,
    DEFAULT_LLM_PROVIDER_MODELS,
    Model,
    _repoint_evaluators,
    _update_llm_provider_models,
    get_default_model,
    update_embedding_provider_models,
    update_llm_provider_models,
)
from apps.service_providers.models import EmbeddingProviderModel, LlmProviderModel
from apps.utils.factories.evaluations import EvaluatorFactory
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
def test_old_models_are_not_removed():
    old_global_model = LlmProviderModelFactory.create(team=None)

    update_llm_provider_models()

    assert LlmProviderModel.objects.filter(team=None, type=old_global_model.type, name=old_global_model.name).exists()


@pytest.mark.django_db()
def test_converts_custom_models_to_global_models_pipelines():
    custom_model = LlmProviderModelFactory.create()
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


@pytest.mark.django_db()
def test_converts_custom_models_to_global_models_evaluators():
    custom_model = LlmProviderModelFactory.create()
    evaluator = EvaluatorFactory.create(team=custom_model.team, params={"llm_provider_model_id": custom_model.id})
    assert evaluator.llm_provider_model_id == custom_model.id

    defaults = {custom_model.type: [Model(custom_model.name, custom_model.max_token_limit)]}
    with patch("apps.service_providers.llm_service.default_models.DEFAULT_LLM_PROVIDER_MODELS", defaults):
        update_llm_provider_models()

    global_model = LlmProviderModel.objects.get(team=None, type=custom_model.type, name=custom_model.name)
    evaluator.refresh_from_db()
    assert evaluator.llm_provider_model_id == global_model.id
    assert evaluator.params["llm_provider_model_id"] == global_model.id


@pytest.mark.django_db()
def test_converts_custom_models_to_global_models_from_a_migration():
    """``_update_llm_provider_models`` also runs from migrations, with historical models.

    Historical models carry no custom methods, so the evaluator repointing has to work
    without ``Evaluator.set_llm_provider_model_id``.
    """
    custom_model = LlmProviderModelFactory.create()
    evaluator = EvaluatorFactory.create(team=custom_model.team, params={"llm_provider_model_id": custom_model.id})

    historical_state = MigrationExecutor(connection).loader.project_state()
    HistoricalLlmProviderModel = historical_state.apps.get_model("service_providers", "LlmProviderModel")

    defaults = {custom_model.type: [Model(custom_model.name, custom_model.max_token_limit)]}
    with patch("apps.service_providers.llm_service.default_models.DEFAULT_LLM_PROVIDER_MODELS", defaults):
        _update_llm_provider_models(HistoricalLlmProviderModel)

    global_model = LlmProviderModel.objects.get(team=None, type=custom_model.type, name=custom_model.name)
    evaluator.refresh_from_db()
    assert evaluator.llm_provider_model_id == global_model.id
    assert evaluator.params["llm_provider_model_id"] == global_model.id


@pytest.mark.django_db()
def test_repointing_raises_when_the_evaluator_relation_is_missing_but_its_fk_is_live():
    """A migration state predating evaluations.0018 cannot see a constraint that still binds.

    ``_replace_custom_model_with_global`` deletes the custom model, so skipping the repoint
    here would surface as a deferred FK violation at commit rather than at the cause.
    """
    custom_model = LlmProviderModelFactory.create()
    global_model = LlmProviderModelFactory.create(team=None, type=custom_model.type, name=custom_model.name)
    # Stands in for a historical LlmProviderModel from a state without evaluations.Evaluator,
    # which has no ``evaluators`` reverse accessor at all.
    pre_0018_model = SimpleNamespace(id=custom_model.id, type=custom_model.type, name=custom_model.name)

    with pytest.raises(RuntimeError, match="0018_evaluator_llm_provider_fks"):
        _repoint_evaluators(pre_0018_model, global_model)


@pytest.mark.django_db()
def test_repointing_skips_quietly_when_the_evaluator_fk_is_not_in_the_database_yet():
    """Before evaluations.0018 is applied there is no constraint and nothing to repoint."""
    global_model = LlmProviderModelFactory.create(team=None)
    pre_0018_model = SimpleNamespace(id=global_model.id + 1, type="openai", name="custom")

    with patch(
        "apps.service_providers.llm_service.default_models._evaluator_provider_model_fk_in_db", return_value=False
    ):
        _repoint_evaluators(pre_0018_model, global_model)  # must not raise


@pytest.mark.django_db()
def test_voyage_embedding_models_are_seeded():
    update_embedding_provider_models()

    for model_name in DEFAULT_EMBEDDING_PROVIDER_MODELS["voyage"]:
        assert EmbeddingProviderModel.objects.filter(team=None, type="voyage", name=model_name).exists()


def test_model_replacement_defaults_to_none():
    model = Model("gpt-4", 8192)
    assert model.replacement is None


def test_model_replacement_can_be_set():
    model = Model("gpt-4", 8192, deprecated=True, replacement="gpt-4o")
    assert model.replacement == "gpt-4o"


def get_pipeline(llm_provider_model):
    pipeline = PipelineFactory.create()
    node_data = {node.flow_id: None for node in pipeline.node_set.all()}
    node_data["1"] = content_flow_node(
        "1",
        "LLMResponseWithPrompt",
        label="LLM",
        params={
            "llm_provider_model_id": str(llm_provider_model.id),
            "prompt": "You are a helpful assistant",
        },
    )
    pipeline.update_nodes_from_data(node_data)
    return pipeline
