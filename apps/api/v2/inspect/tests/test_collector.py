import pytest

from apps.api.v2.inspect.collector import InspectCollector
from apps.api.v2.inspect.node_walker import (
    COLLECTION,
    CUSTOM_ACTION,
    LLM_PROVIDER,
    LLM_PROVIDER_MODEL,
    SOURCE_MATERIAL,
    SYNTHETIC_VOICE,
    ListRef,
    LlmRef,
    SingleRef,
    VoiceRef,
)
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
def test_llm_pair_flattened():
    team = TeamFactory.create()
    provider = LlmProviderFactory.create(team=team, name="Prod OpenAI", type="openai")
    model = LlmProviderModelFactory.create(team=team, name="gpt-4o", max_token_limit=128000)
    collector = InspectCollector(team).load({LLM_PROVIDER: {provider.id}, LLM_PROVIDER_MODEL: {model.id}})
    out = collector.inline_refs({"llm": LlmRef(provider.id, model.id)})
    assert out["llm"] == {
        "provider_id": provider.id,
        "provider_name": "Prod OpenAI",
        "type": "openai",
        "model": "gpt-4o",
        "max_token_limit": 128000,
        "deprecated": False,
    }


@pytest.mark.django_db()
def test_global_llm_provider_model_loaded():
    team = TeamFactory.create()
    global_model = LlmProviderModelFactory.create(team=None, name="shared-model")
    collector = InspectCollector(team).load({LLM_PROVIDER_MODEL: {global_model.id}})
    out = collector.inline_refs({"llm": LlmRef(None, global_model.id)})
    assert out["llm"]["model"] == "shared-model"


@pytest.mark.django_db()
def test_media_vs_indexed_collection():
    team = TeamFactory.create()
    media = CollectionFactory.create(team=team, is_index=False, llm_provider=None, embedding_provider_model=None)
    CollectionFileFactory.create(collection=media, file=FileFactory.create(team=team, name="returns.pdf"))
    indexed = CollectionFactory.create(
        team=team,
        is_index=True,
        llm_provider=LlmProviderFactory.create(team=team),
        embedding_provider_model=EmbeddingProviderModelFactory.create(team=team),
    )
    collector = InspectCollector(team).load({COLLECTION: {media.id, indexed.id}})
    out = collector.inline_refs(
        {
            "media_collection": SingleRef(COLLECTION, media.id),
            "indexed_collections": ListRef(COLLECTION, [indexed.id]),
        }
    )
    assert "embedding" not in out["media_collection"]
    assert out["media_collection"]["files"][0]["name"] == "returns.pdf"
    assert out["indexed_collections"][0]["embedding"]["model"] == "text-embedding-3-small"


@pytest.mark.django_db()
def test_custom_actions_list():
    team = TeamFactory.create()
    action = CustomActionFactory.create(team=team)
    collector = InspectCollector(team).load({CUSTOM_ACTION: {action.id}})
    out = collector.inline_refs({"custom_actions": ListRef(CUSTOM_ACTION, [action.id])})
    assert len(out["custom_actions"]) == 1
    assert out["custom_actions"][0]["id"] == action.id


@pytest.mark.django_db()
def test_voice_flattened_from_synthetic_voice():
    team = TeamFactory.create()
    provider = VoiceProviderFactory.create(team=team, name="ElevenLabs", type="elevenlabs")
    voice = SyntheticVoiceFactory.create(name="Rachel", language="English", neural=True, voice_provider=provider)
    collector = InspectCollector(team).load({SYNTHETIC_VOICE: {voice.id}})
    out = collector.inline_refs({"voice": VoiceRef(voice.id)})
    assert out["voice"]["voice_name"] == "Rachel"
    assert out["voice"]["provider_name"] == "ElevenLabs"
    assert out["voice"]["neural"] is True


@pytest.mark.django_db()
def test_cross_team_id_resolves_to_absent():
    team = TeamFactory.create()
    other_material = SourceMaterialFactory.create(team=TeamFactory.create())
    collector = InspectCollector(team).load({SOURCE_MATERIAL: {other_material.id}})
    out = collector.inline_refs(
        {
            "single": SingleRef(SOURCE_MATERIAL, other_material.id),
            "list": ListRef(SOURCE_MATERIAL, [other_material.id]),
        }
    )
    # Cross-team id is not in the team-scoped map: single -> None, list -> dropped.
    assert out["single"] is None
    assert out["list"] == []


@pytest.mark.django_db()
def test_duplicate_reference_is_byte_identical_with_same_id():
    team = TeamFactory.create()
    material = SourceMaterialFactory.create(team=team, topic="Returns policy")
    collector = InspectCollector(team).load({SOURCE_MATERIAL: {material.id}})
    a = collector.inline_refs({"source_material": SingleRef(SOURCE_MATERIAL, material.id)})
    b = collector.inline_refs({"source_material": SingleRef(SOURCE_MATERIAL, material.id)})
    assert a["source_material"] == b["source_material"]
    assert a["source_material"]["id"] == material.id


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
        collector = InspectCollector(team).load({COLLECTION: ids})
    # Inlining many sites copies from memory and issues no further queries.
    with django_assert_num_queries(0):
        for collection in collections:
            collector.inline_refs({"media_collection": SingleRef(COLLECTION, collection.id)})
