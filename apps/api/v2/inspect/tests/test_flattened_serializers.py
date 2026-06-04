"""The flattened / selection serializers render resolved instances (ADR-0025) with declared
fields, so the OpenAPI components derived from them are exact."""

import pytest

from apps.api.v2.inspect.serializers import (
    CustomActionSelection,
    CustomActionSerializer,
    FlattenedLlmSerializer,
    FlattenedModelProviderSerializer,
    FlattenedVoiceSerializer,
    IndexedCollectionSerializer,
    MediaCollectionSerializer,
    ProviderModelPair,
    VoicePair,
)
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.experiment import SyntheticVoiceFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    EmbeddingProviderModelFactory,
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_flattened_llm_full_pair():
    team = TeamFactory.create()
    provider = LlmProviderFactory.create(team=team, name="Prod OpenAI", type="openai")
    model = LlmProviderModelFactory.create(team=team, name="gpt-4o", max_token_limit=128000, deprecated=False)
    assert FlattenedLlmSerializer(ProviderModelPair(provider, model)).data == {
        "provider_id": provider.id,
        "provider_name": "Prod OpenAI",
        "type": "openai",
        "model": "gpt-4o",
        "max_token_limit": 128000,
        "deprecated": False,
    }


@pytest.mark.django_db()
def test_flattened_llm_model_only_falls_back_to_model_type():
    model = LlmProviderModelFactory.create(name="gpt-4o")
    data = FlattenedLlmSerializer(ProviderModelPair(None, model)).data
    assert data["provider_id"] is None
    assert data["provider_name"] is None
    assert data["type"] == model.type
    assert data["model"] == "gpt-4o"


@pytest.mark.django_db()
def test_flattened_llm_provider_only_emits_null_model_fields():
    provider = LlmProviderFactory.create(type="openai")
    data = FlattenedLlmSerializer(ProviderModelPair(provider, None)).data
    assert data["type"] == "openai"
    assert data["model"] is None
    assert data["max_token_limit"] is None
    assert data["deprecated"] is None


@pytest.mark.django_db()
def test_flattened_voice():
    provider = VoiceProviderFactory.create(name="ElevenLabs", type="elevenlabs")
    voice = SyntheticVoiceFactory.create(name="Rachel", language="English", neural=True, voice_provider=provider)
    assert FlattenedVoiceSerializer(VoicePair(provider, voice)).data == {
        "provider_id": provider.id,
        "provider_name": "ElevenLabs",
        "type": "elevenlabs",
        "voice_name": "Rachel",
        "language": "English",
        "neural": True,
    }


@pytest.mark.django_db()
def test_flattened_embedding():
    team = TeamFactory.create()
    provider = LlmProviderFactory.create(team=team, name="Prod OpenAI", type="openai")
    model = EmbeddingProviderModelFactory.create(team=team, name="text-embedding-3-small")
    assert FlattenedModelProviderSerializer(ProviderModelPair(provider, model)).data == {
        "provider_id": provider.id,
        "provider_name": "Prod OpenAI",
        "type": "openai",
        "model": "text-embedding-3-small",
    }


@pytest.mark.django_db()
def test_flattened_embedding_model_only_falls_back_to_model_type():
    model = EmbeddingProviderModelFactory.create(name="text-embedding-3-small")
    data = FlattenedModelProviderSerializer(ProviderModelPair(None, model)).data
    assert data["provider_id"] is None
    assert data["type"] == model.type
    assert data["model"] == "text-embedding-3-small"


@pytest.mark.django_db()
def test_flattened_voice_provider_only_emits_null_voice_fields():
    provider = VoiceProviderFactory.create(type="elevenlabs")
    data = FlattenedVoiceSerializer(VoicePair(provider, None)).data
    assert data["type"] == provider.type
    assert data["voice_name"] is None
    assert data["neural"] is None


@pytest.mark.django_db()
def test_media_collection_has_no_embedding_key():
    media = CollectionFactory.create(is_index=False, llm_provider=None, embedding_provider_model=None)
    CollectionFileFactory.create(collection=media, file=FileFactory.create(team=media.team, name="returns.pdf"))
    data = MediaCollectionSerializer(media).data
    assert "embedding" not in data
    assert data["files"][0]["name"] == "returns.pdf"


@pytest.mark.django_db()
def test_indexed_collection_embeds_embedding_pair():
    model = EmbeddingProviderModelFactory.create()
    indexed = CollectionFactory.create(
        is_index=True,
        llm_provider=LlmProviderFactory.create(),
        embedding_provider_model=model,
    )
    assert IndexedCollectionSerializer(indexed).data["embedding"]["model"] == model.name


@pytest.mark.django_db()
def test_indexed_collection_without_embedding_pair_is_null():
    indexed = CollectionFactory.create(is_index=True, llm_provider=None, embedding_provider_model=None)
    assert IndexedCollectionSerializer(indexed).data["embedding"] is None


@pytest.mark.django_db()
def test_custom_action_renders_selected_operations_only():
    action = CustomActionFactory.create(auth_provider=AuthProviderFactory.create())
    data = CustomActionSerializer(CustomActionSelection(action, ["weather_get"])).data
    assert data["id"] == action.id
    assert data["allowed_operations"] == ["weather_get"]
    assert data["api_schema"] == {"paths": ["/weather"]}
    assert set(data["auth_provider"].keys()) == {"id", "type", "name"}


@pytest.mark.django_db()
def test_custom_action_unknown_operation_resolves_to_absent():
    action = CustomActionFactory.create()
    data = CustomActionSerializer(CustomActionSelection(action, ["no_such_op"])).data
    assert data["allowed_operations"] == []
    assert data["api_schema"] == {"paths": []}
