import pytest

from apps.api.v2.inspect.collector import InspectCollector
from apps.api.v2.inspect.node_walker import (
    CustomActionsRef,
    ListRef,
    LlmRef,
    ResourceKind,
    SingleRef,
    VoiceRef,
)
from apps.api.v2.inspect.serializers import CustomActionSelection, ProviderModelPair, VoicePair
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.experiment import SourceMaterialFactory, SyntheticVoiceFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import (
    EmbeddingProviderModelFactory,
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_llm_ref_resolves_to_pair():
    team = TeamFactory.create()
    provider = LlmProviderFactory.create(team=team)
    model = LlmProviderModelFactory.create(team=team)
    collector = InspectCollector(team).load(
        {ResourceKind.LLM_PROVIDER: {provider.id}, ResourceKind.LLM_PROVIDER_MODEL: {model.id}}
    )
    out = collector.resolve_refs({"llm": LlmRef(provider.id, model.id)})
    assert out["llm"] == ProviderModelPair(provider, model)


@pytest.mark.django_db()
def test_llm_ref_with_nothing_set_resolves_to_none():
    collector = InspectCollector(TeamFactory.create()).load({})
    assert collector.resolve_refs({"llm": LlmRef(None, None)}) == {"llm": None}


@pytest.mark.django_db()
def test_llm_ref_with_absent_model_resolves_to_partial_pair():
    team = TeamFactory.create()
    provider = LlmProviderFactory.create(team=team)
    collector = InspectCollector(team).load({ResourceKind.LLM_PROVIDER: {provider.id}})
    out = collector.resolve_refs({"llm": LlmRef(provider.id, None)})
    assert out["llm"] == ProviderModelPair(provider, None)


@pytest.mark.django_db()
def test_global_llm_provider_model_loaded():
    team = TeamFactory.create()
    global_model = LlmProviderModelFactory.create(team=None, name="shared-model")
    collector = InspectCollector(team).load({ResourceKind.LLM_PROVIDER_MODEL: {global_model.id}})
    out = collector.resolve_refs({"llm": LlmRef(None, global_model.id)})
    assert out["llm"] == ProviderModelPair(None, global_model)


@pytest.mark.django_db()
def test_single_and_list_collection_refs_resolve_to_instances():
    team = TeamFactory.create()
    media = CollectionFactory.create(team=team, is_index=False, llm_provider=None, embedding_provider_model=None)
    CollectionFileFactory.create(collection=media, file=FileFactory.create(team=team))
    indexed = CollectionFactory.create(
        team=team,
        is_index=True,
        llm_provider=LlmProviderFactory.create(team=team),
        embedding_provider_model=EmbeddingProviderModelFactory.create(team=team),
    )
    collector = InspectCollector(team).load({ResourceKind.COLLECTION: {media.id, indexed.id}})
    out = collector.resolve_refs(
        {
            "media_collection": SingleRef(ResourceKind.COLLECTION, media.id),
            "indexed_collections": ListRef(ResourceKind.COLLECTION, [indexed.id]),
        }
    )
    assert out["media_collection"] == media
    assert out["indexed_collections"] == [indexed]


@pytest.mark.django_db()
def test_custom_actions_resolve_to_selections():
    team = TeamFactory.create()
    action = CustomActionFactory.create(team=team)
    collector = InspectCollector(team).load({ResourceKind.CUSTOM_ACTION: {action.id}})
    out = collector.resolve_refs({"custom_actions": CustomActionsRef([(action.id, ["weather_get"])])})
    assert out["custom_actions"] == [CustomActionSelection(action, ["weather_get"])]


@pytest.mark.django_db()
def test_voice_ref_resolves_with_its_provider():
    team = TeamFactory.create()
    provider = VoiceProviderFactory.create(team=team)
    voice = SyntheticVoiceFactory.create(voice_provider=provider)
    collector = InspectCollector(team).load({ResourceKind.SYNTHETIC_VOICE: {voice.id}})
    assert collector.resolve_refs({"voice": VoiceRef(voice.id)}) == {"voice": VoicePair(provider, voice)}


@pytest.mark.django_db()
def test_voice_ref_without_provider_resolves_to_pair_with_none_provider():
    team = TeamFactory.create()
    voice = SyntheticVoiceFactory.create(voice_provider=None)
    collector = InspectCollector(team).load({ResourceKind.SYNTHETIC_VOICE: {voice.id}})
    assert collector.resolve_refs({"voice": VoiceRef(voice.id)}) == {"voice": VoicePair(None, voice)}


@pytest.mark.django_db()
def test_synthetic_voice_single_ref_resolves_to_pair():
    # The forward-compat ``OptionsSource.synthetic_voice_id`` registry entry emits a SingleRef
    # under the "voice" payload key, which FlattenedVoiceSerializer renders — so it must resolve
    # to a VoicePair, not a raw SyntheticVoice instance.
    team = TeamFactory.create()
    provider = VoiceProviderFactory.create(team=team)
    voice = SyntheticVoiceFactory.create(voice_provider=provider)
    collector = InspectCollector(team).load({ResourceKind.SYNTHETIC_VOICE: {voice.id}})
    out = collector.resolve_refs({"voice": SingleRef(ResourceKind.SYNTHETIC_VOICE, voice.id)})
    assert out["voice"] == VoicePair(provider, voice)


@pytest.mark.django_db()
def test_voice_provider_single_ref_resolves_to_pair():
    # Same for the forward-compat ``OptionsSource.voice_provider_id`` entry: a provider-only
    # reference resolves to a VoicePair with no voice half.
    team = TeamFactory.create()
    provider = VoiceProviderFactory.create(team=team)
    collector = InspectCollector(team).load({ResourceKind.VOICE_PROVIDER: {provider.id}})
    out = collector.resolve_refs({"voice": SingleRef(ResourceKind.VOICE_PROVIDER, provider.id)})
    assert out["voice"] == VoicePair(provider, None)


@pytest.mark.django_db()
def test_cross_team_id_resolves_to_absent():
    team = TeamFactory.create()
    other_material = SourceMaterialFactory.create(team=TeamFactory.create())
    collector = InspectCollector(team).load({ResourceKind.SOURCE_MATERIAL: {other_material.id}})
    out = collector.resolve_refs(
        {
            "single": SingleRef(ResourceKind.SOURCE_MATERIAL, other_material.id),
            "list": ListRef(ResourceKind.SOURCE_MATERIAL, [other_material.id]),
        }
    )
    # Cross-team id is not in the team-scoped map: single -> None, list -> dropped (ADR-0028).
    assert out["single"] is None
    assert out["list"] == []


@pytest.mark.django_db()
def test_duplicate_reference_resolves_to_the_same_instance():
    team = TeamFactory.create()
    material = SourceMaterialFactory.create(team=team)
    collector = InspectCollector(team).load({ResourceKind.SOURCE_MATERIAL: {material.id}})
    a = collector.resolve_refs({"source_material": SingleRef(ResourceKind.SOURCE_MATERIAL, material.id)})
    b = collector.resolve_refs({"source_material": SingleRef(ResourceKind.SOURCE_MATERIAL, material.id)})
    # The same loaded instance is handed to every reference site, so the serialized copies the
    # response serializers later produce are byte-identical.
    assert a["source_material"] is b["source_material"]


@pytest.mark.django_db()
def test_collection_load_is_not_n_plus_one(django_assert_num_queries):
    """Many nodes referencing collections still load collections + files in a bounded set of
    queries (the N+1 guard)."""
    team = TeamFactory.create()
    collections = [
        CollectionFactory.create(team=team, is_index=False, llm_provider=None, embedding_provider_model=None)
        for _ in range(3)
    ]
    for collection in collections:
        CollectionFileFactory.create(collection=collection, file=FileFactory.create(team=team))
    ids = {c.id for c in collections}
    # 1 query for the collections + 1 for the prefetched files = 2.
    with django_assert_num_queries(2):
        collector = InspectCollector(team).load({ResourceKind.COLLECTION: ids})
    # Resolving many sites copies from memory and issues no further queries.
    with django_assert_num_queries(0):
        for collection in collections:
            collector.resolve_refs({"media_collection": SingleRef(ResourceKind.COLLECTION, collection.id)})
