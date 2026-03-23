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
    response = authed_client.get(
        reverse("service_providers:new", kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug})
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
