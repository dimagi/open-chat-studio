"""Unit tests for the id-collection traversal (pure) and the batch-loading fetcher (DB)."""

import pytest

from apps.api.v2.inspect.nodes import ResourceKind
from apps.api.v2.inspect.resources import ResourceFetcher, iter_resource_refs
from apps.events.models import EventActionType
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory, SourceMaterialFactory, SyntheticVoiceFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory


# ── pure ──────────────────────────────────────────────────────────────────────────────────────
def test_iter_resource_refs_llm_yields_both_halves():
    refs = set(iter_resource_refs("RouterNode", {"llm_provider_id": "2", "llm_provider_model_id": "11"}))
    assert (ResourceKind.LLM_PROVIDER, "2") in refs
    assert (ResourceKind.LLM_PROVIDER_MODEL, "11") in refs


def test_iter_resource_refs_indexed_collections_is_a_list():
    refs = list(iter_resource_refs("LLMResponseWithPrompt", {"collection_index_ids": ["3", "5"]}))
    assert (ResourceKind.COLLECTION, "3") in refs
    assert (ResourceKind.COLLECTION, "5") in refs


def test_iter_resource_refs_custom_actions_yields_action_ids():
    refs = list(iter_resource_refs("LLMResponseWithPrompt", {"custom_actions": ["7:op_a", "7:op_b", "9:op_c"]}))
    assert (ResourceKind.CUSTOM_ACTION, 7) in refs
    assert (ResourceKind.CUSTOM_ACTION, 9) in refs


def test_iter_resource_refs_unknown_node_type_yields_nothing():
    assert list(iter_resource_refs("NoSuchNode", {"llm_provider_id": "2"})) == []


def test_iter_resource_refs_start_node_yields_nothing():
    assert list(iter_resource_refs("StartNode", {"anything": 1})) == []


# ── DB: ResourceFetcher ─────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_fetcher_resolves_team_scoped_resources():
    team = TeamWithUsersFactory.create()
    provider = LlmProviderFactory.create(team=team)
    model = LlmProviderModelFactory.create(team=team)
    source = SourceMaterialFactory.create(team=team)
    pipeline = PipelineFactory.create(team=team)
    NodeFactory.create(
        pipeline=pipeline,
        type="LLMResponseWithPrompt",
        params={
            "llm_provider_id": str(provider.id),
            "llm_provider_model_id": str(model.id),
            "source_material_id": str(source.id),
        },
    )
    experiment = ExperimentFactory.create(team=team, pipeline=pipeline)

    fetcher = ResourceFetcher.for_experiment(experiment)

    assert fetcher.llm_provider(str(provider.id)).id == provider.id
    assert fetcher.llm_provider_model(model.id).id == model.id
    assert fetcher.source_material(source.id).id == source.id


@pytest.mark.django_db()
def test_fetcher_cross_team_id_resolves_to_absent():
    team = TeamWithUsersFactory.create()
    foreign = SourceMaterialFactory.create(team=TeamWithUsersFactory.create())
    pipeline = PipelineFactory.create(team=team)
    NodeFactory.create(pipeline=pipeline, type="LLMResponseWithPrompt", params={"source_material_id": str(foreign.id)})
    experiment = ExperimentFactory.create(team=team, pipeline=pipeline)

    fetcher = ResourceFetcher.for_experiment(experiment)

    assert fetcher.source_material(foreign.id) is None


@pytest.mark.django_db()
def test_fetcher_loads_global_llm_provider_model():
    team = TeamWithUsersFactory.create()
    global_model = LlmProviderModelFactory.create(team=None)
    pipeline = PipelineFactory.create(team=team)
    NodeFactory.create(pipeline=pipeline, type="LLMResponse", params={"llm_provider_model_id": str(global_model.id)})
    experiment = ExperimentFactory.create(team=team, pipeline=pipeline)

    fetcher = ResourceFetcher.for_experiment(experiment)

    assert fetcher.llm_provider_model(global_model.id).id == global_model.id


@pytest.mark.django_db()
def test_fetcher_collects_ids_from_embedded_pipeline_start_pipeline():
    team = TeamWithUsersFactory.create()
    assistant = OpenAiAssistantFactory.create(team=team)
    embedded = PipelineFactory.create(team=team)
    NodeFactory.create(pipeline=embedded, type="AssistantNode", params={"assistant_id": str(assistant.id)})
    experiment = ExperimentFactory.create(team=team, pipeline=PipelineFactory.create(team=team))
    StaticTriggerFactory.create(
        experiment=experiment,
        type="conversation_start",
        action=EventActionFactory.create(
            action_type=EventActionType.PIPELINE_START, params={"pipeline_id": str(embedded.id)}
        ),
    )

    fetcher = ResourceFetcher.for_experiment(experiment)

    assert fetcher.assistant(assistant.id).id == assistant.id
    assert fetcher.embedded_pipeline(str(embedded.id)).id == embedded.id


@pytest.mark.django_db()
def test_fetcher_synthetic_voice_loads_with_provider():
    team = TeamWithUsersFactory.create()
    voice_provider = VoiceProviderFactory.create(team=team)
    voice = SyntheticVoiceFactory.create(voice_provider=voice_provider)
    pipeline = PipelineFactory.create(team=team)
    NodeFactory.create(pipeline=pipeline, type="LLMResponseWithPrompt", params={"synthetic_voice_id": str(voice.id)})
    experiment = ExperimentFactory.create(team=team, pipeline=pipeline)

    fetcher = ResourceFetcher.for_experiment(experiment)

    loaded = fetcher.synthetic_voice(voice.id)
    assert loaded.id == voice.id
    assert loaded.voice_provider_id == voice_provider.id
