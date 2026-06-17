from unittest import mock

import pytest
from django.urls import reverse

from apps.service_providers.models import (
    AuthProvider,
    LlmProvider,
    MessagingProvider,
    TraceProvider,
    VoiceProvider,
    VoiceProviderType,
)
from apps.service_providers.utils import ServiceProvider
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    LlmProviderFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)


def factory_for_model(model):
    factory = {
        LlmProvider: LlmProviderFactory,
        VoiceProvider: VoiceProviderFactory,
        MessagingProvider: MessagingProviderFactory,
        AuthProvider: AuthProviderFactory,
        TraceProvider: TraceProviderFactory,
    }.get(model)

    return factory


@pytest.fixture()
def authed_client(team_with_users, client):
    user = team_with_users.members.first()
    client.force_login(user)
    return client


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_table_view(provider, team_with_users, authed_client):
    factory = factory_for_model(provider.model)
    factory.create_batch(5, team=team_with_users)
    assert provider.model.objects.filter(team=team_with_users).count() == 5

    response = authed_client.get(
        reverse("service_providers:table", kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug})
    )
    assert response.status_code == 200
    assert len(response.context["table"].rows) == 5


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_create_view(provider, team_with_users, authed_client):
    """Test that the create view renders without error."""
    subtype = next(iter(provider.subtype))
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": provider.slug,
                "subtype": str(subtype),
            },
        )
    )
    assert response.status_code == 200


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_update_view(provider, team_with_users, authed_client):
    """Test that the update view renders without error."""
    factory = factory_for_model(provider.model)
    provider_instance = factory(team=team_with_users)
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug, "pk": provider_instance.pk},
        )
    )
    assert response.status_code == 200


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_delete_view(provider, team_with_users, authed_client):
    factory = factory_for_model(provider.model)
    provider_instance = factory(team=team_with_users)
    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug, "pk": provider_instance.pk},
        )
    )
    assert response.status_code == 200
    assert provider.model.objects.filter(team=team_with_users).count() == 0


@pytest.mark.django_db()
def test_sync_voices_endpoint(team_with_users, authed_client):
    """POST to sync-voices endpoint should call sync_voices on the provider"""

    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    url = reverse(
        "service_providers:sync_voices",
        kwargs={
            "team_slug": team_with_users.slug,
            "provider_type": "voice",
            "pk": provider.pk,
        },
    )
    with mock.patch.object(VoiceProvider, "sync_voices") as mock_sync:
        response = authed_client.post(url)

    assert response.status_code == 302
    mock_sync.assert_called_once()


@pytest.mark.django_db()
def test_delete_llm_provider_referenced_by_pipeline_nullifies_node_fk(team_with_users, authed_client):
    """Deleting an LLM provider referenced by a pipeline node succeeds (SET_NULL): the node's
    llm_provider FK is nulled, while params (authoritative) is left untouched."""
    provider = LlmProviderFactory(team=team_with_users)
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": provider.id},
        llm_provider=provider,
    )

    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": ServiceProvider.llm.slug, "pk": provider.pk},
        )
    )

    assert response.status_code == 200
    assert not LlmProvider.objects.filter(pk=provider.pk).exists()
    node.refresh_from_db()
    assert node.llm_provider_id is None
    assert node.params["llm_provider_id"] == provider.id  # params unchanged (authoritative)


@pytest.mark.django_db()
def test_create_view_404_for_filtered_subtype(team_with_users, authed_client, settings):
    """openai_voice_engine is gated by the flag_open_ai_voice_engine flag."""
    settings.SLACK_ENABLED = True  # ensure unrelated filter is off
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": "voice",
                "subtype": VoiceProviderType.openai_voice_engine.value,
            },
        )
    )
    assert response.status_code == 404
