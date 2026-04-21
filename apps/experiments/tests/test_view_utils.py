import pytest
from django.core.cache import cache

from apps.experiments.views.utils import _CHARS_PER_TOKEN, get_max_char_limit
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db()
def test_returns_none_when_no_pipeline():
    experiment = ExperimentFactory(pipeline=None)
    assert get_max_char_limit(experiment) is None


@pytest.mark.django_db()
def test_returns_none_when_no_llm_nodes(db):
    experiment = ExperimentFactory()
    assert get_max_char_limit(experiment) is None


@pytest.mark.django_db()
def test_returns_none_when_no_model_id(db):
    experiment = ExperimentFactory()
    NodeFactory(pipeline=experiment.pipeline, type=LLMResponseWithPrompt.__name__, params={})
    assert get_max_char_limit(experiment) is None


@pytest.mark.django_db()
def test_returns_none_when_model_has_no_token_limit(db):
    experiment = ExperimentFactory()
    model = LlmProviderModelFactory(team=experiment.team, max_token_limit=0)
    NodeFactory(
        pipeline=experiment.pipeline,
        type=LLMResponseWithPrompt.__name__,
        params={"llm_provider_model_id": model.id},
    )
    assert get_max_char_limit(experiment) is None


@pytest.mark.django_db()
def test_returns_char_limit_based_on_token_limit(db):
    experiment = ExperimentFactory()
    model = LlmProviderModelFactory(team=experiment.team, max_token_limit=4096)
    NodeFactory(
        pipeline=experiment.pipeline,
        type=LLMResponseWithPrompt.__name__,
        params={"llm_provider_model_id": model.id},
    )
    result = get_max_char_limit(experiment)
    assert result == 4096 * _CHARS_PER_TOKEN


@pytest.mark.django_db()
def test_returns_min_token_limit_across_llm_nodes(db):
    experiment = ExperimentFactory()
    model_small = LlmProviderModelFactory(team=experiment.team, max_token_limit=2048)
    model_large = LlmProviderModelFactory(team=experiment.team, max_token_limit=8192)
    NodeFactory(
        pipeline=experiment.pipeline,
        type=LLMResponseWithPrompt.__name__,
        params={"llm_provider_model_id": model_small.id},
    )
    NodeFactory(
        pipeline=experiment.pipeline,
        type=LLMResponseWithPrompt.__name__,
        params={"llm_provider_model_id": model_large.id},
    )
    assert get_max_char_limit(experiment) == 2048 * _CHARS_PER_TOKEN


@pytest.mark.django_db()
def test_result_is_cached(db):
    experiment = ExperimentFactory()
    model = LlmProviderModelFactory(team=experiment.team, max_token_limit=4096)
    NodeFactory(
        pipeline=experiment.pipeline,
        type=LLMResponseWithPrompt.__name__,
        params={"llm_provider_model_id": model.id},
    )
    result1 = get_max_char_limit(experiment)
    # Modify the model limit — cached result should be returned
    model.max_token_limit = 1024
    model.save()
    result2 = get_max_char_limit(experiment)
    assert result1 == result2


@pytest.mark.django_db()
def test_cache_busted_by_pipeline_update(db):
    experiment = ExperimentFactory()
    model = LlmProviderModelFactory(team=experiment.team, max_token_limit=4096)
    NodeFactory(
        pipeline=experiment.pipeline,
        type=LLMResponseWithPrompt.__name__,
        params={"llm_provider_model_id": model.id},
    )
    result1 = get_max_char_limit(experiment)

    # Touch pipeline updated_at to bust the cache key
    pipeline = experiment.pipeline
    pipeline.save()  # auto_now updates updated_at
    experiment.refresh_from_db()

    # Now change the model limit — new cache key should recompute
    model.max_token_limit = 1024
    model.save()
    result2 = get_max_char_limit(experiment)
    assert result2 == 1024 * _CHARS_PER_TOKEN
    assert result1 != result2
