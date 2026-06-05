"""Every inspect serializer lists its fields explicitly, so secrets never appear (ADR-0027)."""

import json

import pytest

from apps.api.v2.inspect.serializers import (
    ChannelSerializer,
    CustomActionSelection,
    CustomActionSerializer,
    FileSerializer,
    IndexedCollectionSerializer,
    MediaCollectionSerializer,
    ProviderSerializer,
)
from apps.channels.models import ChannelPlatform
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
def test_provider_serializer_excludes_config(factory):
    provider = factory.create()
    ref = ProviderSerializer(provider).data
    assert set(ref.keys()) == {"id", "type", "name"}
    assert "config" not in json.dumps(ref)
    # The encrypted secret values must never appear anywhere in the projection.
    for secret in provider.config.values():
        assert str(secret) not in json.dumps(ref)


@pytest.mark.django_db()
def test_custom_action_excludes_auth_config_and_digests_schema():
    action = CustomActionFactory.create(auth_provider=AuthProviderFactory.create())
    data = CustomActionSerializer(CustomActionSelection(action, ["weather_get"])).data
    blob = json.dumps(data)
    assert "config" not in blob
    assert set(data["auth_provider"].keys()) == {"id", "type", "name"}
    # only the selected operations are rendered — never the full OpenAPI document
    assert data["allowed_operations"] == ["weather_get"]
    # api_schema reduced to a path digest of the selected operations, not the full OpenAPI document
    assert set(data["api_schema"].keys()) == {"paths"}
    assert data["api_schema"]["paths"] == ["/weather"]
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


# platform, name and messaging_provider are present on every channel; identifier fields are added
# on top of these only for the platform they belong to.
_BASE_CHANNEL_KEYS = {"platform", "name", "messaging_provider"}


@pytest.mark.django_db()
def test_channel_serializer_excludes_extra_data():
    channel = ExperimentChannelFactory.create(
        platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "super-secret"}
    )
    data = ChannelSerializer(channel).data
    # The telegram identifier is its bot_token (a secret), so no identifier field is surfaced for it.
    assert set(data.keys()) == _BASE_CHANNEL_KEYS
    assert "super-secret" not in json.dumps(data)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("platform", "extra_data", "expected_fields"),
    [
        (ChannelPlatform.WHATSAPP, {"number": "+27821234567"}, {"number": "+27821234567"}),
        (ChannelPlatform.FACEBOOK, {"page_id": "123456"}, {"page_id": "123456"}),
        (ChannelPlatform.SUREADHERE, {"sureadhere_tenant_id": "tnt_9"}, {"sureadhere_tenant_id": "tnt_9"}),
        (
            ChannelPlatform.COMMCARE_CONNECT,
            {"commcare_connect_bot_name": "my-bot"},
            {"commcare_connect_bot_name": "my-bot"},
        ),
        (ChannelPlatform.TELEGRAM, {"bot_token": "secret"}, {}),
        (ChannelPlatform.SLACK, {"slack_channel_id": "C123"}, {}),
    ],
)
def test_channel_identifier_fields_per_platform(platform, extra_data, expected_fields):
    data = ChannelSerializer(ExperimentChannelFactory.create(platform=platform, extra_data=extra_data)).data
    assert set(data.keys()) == _BASE_CHANNEL_KEYS | set(expected_fields)
    for key, value in expected_fields.items():
        assert data[key] == value


@pytest.mark.django_db()
def test_embedded_widget_identifier_allow_all_domains():
    channel = ExperimentChannelFactory.create(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"allowed_domains": ["*"], "widget_token": "super-secret"},
    )
    data = ChannelSerializer(channel).data
    assert set(data.keys()) == _BASE_CHANNEL_KEYS | {"allow_all_domains", "allowed_domains"}
    assert data["allow_all_domains"] is True
    assert data["allowed_domains"] == []
    # The widget_token is a secret and must never be surfaced.
    assert "super-secret" not in json.dumps(data)


@pytest.mark.django_db()
def test_embedded_widget_identifier_explicit_domains():
    channel = ExperimentChannelFactory.create(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"allowed_domains": ["acme.com", "foo.org"], "widget_token": "super-secret"},
    )
    data = ChannelSerializer(channel).data
    assert data["allow_all_domains"] is False
    assert data["allowed_domains"] == ["acme.com", "foo.org"]
    assert "super-secret" not in json.dumps(data)


@pytest.mark.django_db()
def test_channel_serializer_embeds_messaging_provider_without_config():
    provider = MessagingProviderFactory.create()
    channel = ExperimentChannelFactory.create(messaging_provider=provider)
    data = ChannelSerializer(channel).data
    assert data["messaging_provider"] == {"id": provider.id, "type": provider.type, "name": provider.name}

    bare = ChannelSerializer(ExperimentChannelFactory.create()).data
    assert bare["messaging_provider"] is None


@pytest.mark.django_db()
def test_collection_media_vs_indexed():
    media = CollectionFactory.create(is_index=False)
    CollectionFileFactory.create(collection=media, file=FileFactory.create(team=media.team))
    media_data = MediaCollectionSerializer(media).data
    assert "embedding" not in media_data
    assert len(media_data["files"]) == 1

    indexed = CollectionFactory.create(
        is_index=True,
        llm_provider=LlmProviderFactory.create(),
        embedding_provider_model=EmbeddingProviderModelFactory.create(),
    )
    indexed_data = IndexedCollectionSerializer(indexed).data
    assert indexed_data["embedding"]["model"] == "text-embedding-3-small"
    assert "config" not in json.dumps(indexed_data)
