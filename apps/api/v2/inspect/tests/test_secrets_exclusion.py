"""ADR-0027: every inspect serializer is an explicit allowlist; secrets never appear."""

import json

import pytest

from apps.api.v2.inspect.serializers import (
    ChannelSerializer,
    FileSerializer,
    provider_ref,
    serialize_collection,
    serialize_custom_action,
)
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    EmbeddingProviderModelFactory,
    LlmProviderFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "factory",
    [LlmProviderFactory, VoiceProviderFactory, MessagingProviderFactory, AuthProviderFactory, TraceProviderFactory],
)
def test_provider_ref_excludes_config(factory):
    provider = factory.create()
    ref = provider_ref(provider)
    assert set(ref.keys()) == {"id", "type", "name"}
    assert "config" not in json.dumps(ref)
    # The encrypted secret values must never appear anywhere in the projection.
    for secret in provider.config.values():
        assert str(secret) not in json.dumps(ref)


def test_provider_ref_none():
    assert provider_ref(None) is None


@pytest.mark.django_db()
def test_custom_action_excludes_auth_config_and_digests_schema():
    action = CustomActionFactory.create(auth_provider=AuthProviderFactory.create())
    data = serialize_custom_action(action)
    blob = json.dumps(data)
    assert "config" not in blob
    assert set(data["auth_provider"].keys()) == {"id", "type", "name"}
    # api_schema reduced to a path digest, not the full OpenAPI document
    assert set(data["api_schema"].keys()) == {"paths"}
    assert isinstance(data["api_schema"]["paths"], list)
    assert "securitySchemes" not in blob


@pytest.mark.django_db()
def test_file_serializer_excludes_url_summary_metadata():
    file = FileFactory.create(summary="secret summary", metadata={"citation_url": "https://signed"})
    data = FileSerializer(file).data
    assert set(data.keys()) == {
        "id",
        "name",
        "content_type",
        "content_size",
        "external_source",
        "external_id",
        "purpose",
    }
    blob = json.dumps(data)
    assert "secret summary" not in blob
    assert "citation_url" not in blob
    assert "file" not in data


@pytest.mark.django_db()
def test_channel_serializer_excludes_extra_data():
    channel = ExperimentChannelFactory.create(extra_data={"bot_token": "super-secret"})
    data = ChannelSerializer(channel).data
    assert set(data.keys()) == {"platform", "name"}
    assert "super-secret" not in json.dumps(data)


@pytest.mark.django_db()
def test_collection_media_vs_indexed():
    media = CollectionFactory.create(is_index=False)
    CollectionFileFactory.create(collection=media, file=FileFactory.create(team=media.team))
    media_data = serialize_collection(media, with_embedding=False)
    assert "embedding" not in media_data
    assert len(media_data["files"]) == 1

    indexed = CollectionFactory.create(
        is_index=True,
        llm_provider=LlmProviderFactory.create(),
        embedding_provider_model=EmbeddingProviderModelFactory.create(),
    )
    indexed_data = serialize_collection(indexed, with_embedding=True)
    assert indexed_data["embedding"]["model"] == "text-embedding-3-small"
    assert "config" not in json.dumps(indexed_data)
