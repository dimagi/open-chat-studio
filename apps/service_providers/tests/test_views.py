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
from apps.utils.factories.experiment import ExperimentFactory
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


@pytest.mark.django_db()
class TestDeleteServiceProviderReferenceChecks:
    """Tests that deletion is blocked when any non-archived version references the service provider."""

    @pytest.fixture()
    def voice_provider(self, team_with_users):
        return VoiceProviderFactory.create(team=team_with_users)

    def _delete_url(self, team_with_users, voice_provider):
        return reverse(
            "service_providers:delete",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": "voice",
                "pk": voice_provider.pk,
            },
        )

    def test_delete_blocked_by_working_version(self, team_with_users, authed_client, voice_provider):
        """Deletion is blocked when the working (unreleased) experiment references the provider."""
        ExperimentFactory.create(team=team_with_users, voice_provider=voice_provider)
        response = authed_client.delete(self._delete_url(team_with_users, voice_provider))
        assert response.status_code == 400
        assert VoiceProvider.objects.filter(pk=voice_provider.pk).exists()

    def test_delete_blocked_by_non_published_version(self, team_with_users, authed_client, voice_provider):
        """Deletion is blocked when a non-published (versioned) experiment references the provider.

        The first create_new_version() call auto-sets is_default_version=True (version_number=1),
        so we create a second version which is non-published.
        """
        working_exp = ExperimentFactory.create(team=team_with_users)
        working_exp.create_new_version()  # v1 auto-becomes published
        # v2 is non-published (version_number=2, is_default_version=False)
        non_published = working_exp.create_new_version()
        non_published.voice_provider = voice_provider
        non_published.save()

        response = authed_client.delete(self._delete_url(team_with_users, voice_provider))
        assert response.status_code == 400
        assert VoiceProvider.objects.filter(pk=voice_provider.pk).exists()

    def test_delete_blocked_by_published_version(self, team_with_users, authed_client, voice_provider):
        """Deletion is blocked when the published experiment version references the provider.

        The first create_new_version() call auto-sets is_default_version=True.
        """
        working_exp = ExperimentFactory.create(team=team_with_users)
        published = working_exp.create_new_version()  # auto is_default_version=True
        published.voice_provider = voice_provider
        published.save()

        response = authed_client.delete(self._delete_url(team_with_users, voice_provider))
        assert response.status_code == 400

    def test_delete_allowed_when_only_archived_version_references(self, team_with_users, authed_client, voice_provider):
        """Deletion is allowed when only archived versions reference the provider."""
        working_exp = ExperimentFactory.create(team=team_with_users)
        working_exp.create_new_version()  # v1 auto-becomes published; we won't archive it
        non_published = working_exp.create_new_version()  # v2
        non_published.voice_provider = voice_provider
        non_published.is_archived = True
        non_published.save()

        response = authed_client.delete(self._delete_url(team_with_users, voice_provider))
        assert response.status_code == 200
        assert not VoiceProvider.objects.filter(pk=voice_provider.pk).exists()

    def test_bulk_archiveable_experiments_listed_in_response(self, team_with_users, authed_client, voice_provider):
        """Non-published versions appear in the bulk-archiveable section of the modal response."""
        working_exp = ExperimentFactory.create(team=team_with_users)
        working_exp.create_new_version()  # v1 auto-becomes published
        non_published = working_exp.create_new_version()  # v2 = non-published
        non_published.voice_provider = voice_provider
        non_published.save()

        response = authed_client.delete(self._delete_url(team_with_users, voice_provider))
        assert response.status_code == 400
        content = response.content.decode()
        assert "Non-published versions" in content
        assert "Archive All Non-published Versions" in content


@pytest.mark.django_db()
class TestBulkArchiveExperimentVersions:
    """Tests for the bulk archive endpoint."""

    @pytest.fixture()
    def authed_client(self, team_with_users, client):
        client.force_login(team_with_users.members.first())
        return client

    def test_bulk_archive_non_published_versions(self, team_with_users, authed_client):
        """Non-published, non-working versions are archived.

        v1 auto-becomes published (is_default_version=True); v2 and v3 are non-published.
        """
        working_exp = ExperimentFactory.create(team=team_with_users)
        working_exp.create_new_version()  # v1 auto-becomes published; not passed to endpoint
        v2 = working_exp.create_new_version()  # non-published
        v3 = working_exp.create_new_version()  # non-published

        url = reverse("experiments:bulk_archive_versions", args=[team_with_users.slug])
        response = authed_client.post(url, data={"version_ids": [v2.id, v3.id]})

        assert response.status_code == 200
        v2.refresh_from_db()
        v3.refresh_from_db()
        assert v2.is_archived
        assert v3.is_archived
        # Working version must not be archived
        working_exp.refresh_from_db()
        assert not working_exp.is_archived

    def test_bulk_archive_ignores_published_version(self, team_with_users, authed_client):
        """Published (default) versions are not archived via this endpoint.

        The first create_new_version() call auto-sets is_default_version=True.
        """
        working_exp = ExperimentFactory.create(team=team_with_users)
        published = working_exp.create_new_version()  # auto is_default_version=True

        url = reverse("experiments:bulk_archive_versions", args=[team_with_users.slug])
        response = authed_client.post(url, data={"version_ids": [published.id]})

        assert response.status_code == 200
        published.refresh_from_db()
        assert not published.is_archived

    def test_bulk_archive_ignores_working_version(self, team_with_users, authed_client):
        """Working versions are not archived via this endpoint."""
        working_exp = ExperimentFactory.create(team=team_with_users)

        url = reverse("experiments:bulk_archive_versions", args=[team_with_users.slug])
        response = authed_client.post(url, data={"version_ids": [working_exp.id]})

        assert response.status_code == 200
        working_exp.refresh_from_db()
        assert not working_exp.is_archived

    def test_bulk_archive_empty_ids_returns_400(self, team_with_users, authed_client):
        """Empty version_ids returns 400."""
        url = reverse("experiments:bulk_archive_versions", args=[team_with_users.slug])
        response = authed_client.post(url, data={})
        assert response.status_code == 400

    def test_bulk_archive_cross_team_denied(self, team_with_users, authed_client):
        """Cannot archive versions belonging to another team."""
        other_exp = ExperimentFactory.create()
        other_exp.create_new_version()  # v1 auto-becomes published
        other_version = other_exp.create_new_version()  # v2 non-published, different team

        url = reverse("experiments:bulk_archive_versions", args=[team_with_users.slug])
        response = authed_client.post(url, data={"version_ids": [other_version.id]})

        assert response.status_code == 200
        other_version.refresh_from_db()
        assert not other_version.is_archived

    def test_bulk_archive_returns_hx_refresh(self, team_with_users, authed_client):
        """On success the endpoint returns HX-Refresh header to reload the page."""
        working_exp = ExperimentFactory.create(team=team_with_users)
        working_exp.create_new_version()  # v1 auto-becomes published
        v2 = working_exp.create_new_version()  # v2 non-published

        url = reverse("experiments:bulk_archive_versions", args=[team_with_users.slug])
        response = authed_client.post(url, data={"version_ids": [v2.id]})

        assert response.status_code == 200
        assert response.headers.get("HX-Refresh") == "true"
