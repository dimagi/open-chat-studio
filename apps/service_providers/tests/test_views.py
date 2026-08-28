import re
from datetime import timedelta
from unittest import mock

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.exceptions import ServiceWindowExpiredException
from apps.service_providers.messaging_service import MetaCloudAPIService
from apps.service_providers.models import (
    AuthProvider,
    LlmProvider,
    MessagingProvider,
    MessagingProviderType,
    TraceProvider,
    VoiceProvider,
    VoiceProviderType,
)
from apps.service_providers.utils import ServiceProvider
from apps.utils.factories.evaluations import EvaluatorFactory
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
    llm_provider FK is nulled, while params (authoritative) is left untouched.

    In practice this only happens for an archived pipeline: the delete guards block removing a
    provider that a live (working) node still references, so the FK is only nulled once the
    pipeline holding the node has been archived and the provider is then deleted.
    """
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
def test_delete_llm_provider_blocked_by_an_evaluator(team_with_users, authed_client):
    """Evaluators reference the provider by FK, so deleting underneath one is blocked.

    Previously they were collected by get_related_objects but silently dropped, leaving the
    evaluator with a nulled FK and nothing to run against — with no warning.
    """
    provider = LlmProviderFactory(team=team_with_users)
    evaluator = EvaluatorFactory.create(team=team_with_users, llm_provider=provider)

    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": ServiceProvider.llm.slug, "pk": provider.pk},
        )
    )

    assert response.status_code == 200
    assert evaluator.name in response.content.decode()
    assert LlmProvider.objects.filter(pk=provider.pk).exists()
    evaluator.refresh_from_db()
    assert evaluator.llm_provider_id == provider.id


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


@pytest.fixture()
def meta_provider(team_with_users):
    return MessagingProviderFactory(
        team=team_with_users,
        type=MessagingProviderType.meta_cloud_api,
        config={
            "business_id": "1285815180126064",
            "access_token": "token",
            "app_secret": "secret",
            "verify_token": "verify",
        },
        extra_data={
            "verify_token_hash": "abc123",
            "whatsapp_numbers": {
                "state": "ok",
                "synced_at": "2026-08-28T08:00:00+00:00",
                "numbers": [
                    {
                        "phone_number_id": "1020671484465717",
                        "number": "+27647084804",
                        "display": "+27 64 708 4804",
                        "verified_name": "TenantHive",
                    }
                ],
            },
        },
    )


def _whatsapp_url(name, provider):
    return reverse("service_providers:" + name, kwargs={"team_slug": provider.team.slug, "pk": provider.pk})


def _cache_template(provider, **status):
    provider.extra_data["whatsapp_template"] = {
        "ok": False,
        "checked_at": "2026-08-28T08:00:00+00:00",
        "problems": [],
        "error": None,
        "template": None,
        **status,
    }
    provider.save()
    return provider


@pytest.mark.django_db()
class TestWhatsappStatusView:
    """The panel renders from the cache. Only a refresh talks to Meta."""

    def test_never_calls_meta(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "check_message_template") as check:
            response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.status_code == 200
        check.assert_not_called()

    def test_renders_the_cached_check(self, meta_provider, authed_client):
        _cache_template(meta_provider, ok=True, template={"status": "APPROVED", "language": "en"})

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.context["template_ok"] is True
        assert response.context["template_checked"] is True
        assert "new_bot_message" in response.content.decode()

    def test_renders_a_cached_error(self, meta_provider, authed_client):
        _cache_template(meta_provider, error="(#190) Error validating access token")

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert "Error validating access token" in response.content.decode()

    def test_says_so_when_the_template_has_never_been_checked(self, meta_provider, authed_client):
        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.context["template_checked"] is False
        assert response.context["template_ok"] is False
        assert "has not been checked with Meta yet" in response.content.decode()

    def test_the_refresh_button_targets_the_whole_panel(self, meta_provider, authed_client):
        """One refresh redraws the template block and the numbers together, so they never disagree."""
        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert 'id="wa-status"' in content
        assert _whatsapp_url("whatsapp_refresh", meta_provider) in content
        assert 'hx-target="#wa-status"' in content

    def test_polls_itself_while_a_refresh_is_running(self, meta_provider, authed_client):
        meta_provider.mark_whatsapp_numbers_syncing()

        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert _whatsapp_url("whatsapp_status", meta_provider) in content
        assert 'hx-trigger="every 2s"' in content
        assert "Checking with Meta" in content

    def test_404_for_a_provider_of_another_type(self, team_with_users, authed_client):
        provider = MessagingProviderFactory(team=team_with_users, type=MessagingProviderType.twilio)

        response = authed_client.get(_whatsapp_url("whatsapp_status", provider))

        assert response.status_code == 404

    def test_404_for_another_teams_provider(self, authed_client, team_with_users):
        other_provider = MessagingProviderFactory(type=MessagingProviderType.meta_cloud_api, config={})
        url = reverse(
            "service_providers:whatsapp_status",
            kwargs={"team_slug": team_with_users.slug, "pk": other_provider.pk},
        )

        assert authed_client.get(url).status_code == 404


@pytest.mark.django_db()
class TestWhatsappRefresh:
    """One button, one task: the numbers and the template are re-fetched together."""

    def test_queues_a_refresh(self, meta_provider, authed_client, django_capture_on_commit_callbacks):
        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        assert response.status_code == 200
        delay.assert_called_once_with(meta_provider.pk)
        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers_info["state"] == "pending"

    def test_does_not_queue_a_second_refresh_while_one_is_running(
        self, meta_provider, authed_client, django_capture_on_commit_callbacks
    ):
        meta_provider.mark_whatsapp_numbers_syncing()

        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        delay.assert_not_called()

    def test_queues_a_refresh_when_the_running_one_has_stalled(
        self, meta_provider, authed_client, django_capture_on_commit_callbacks
    ):
        stalled = timezone.now() - timedelta(minutes=5)
        meta_provider.extra_data["whatsapp_numbers"] = {"state": "pending", "started_at": stalled.isoformat()}
        meta_provider.save()

        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        delay.assert_called_once_with(meta_provider.pk)


@pytest.mark.django_db()
class TestWhatsappTestSend:
    def _post(self, client, provider, **overrides):
        data = {
            "from_number_id": "1020671484465717",
            "to_number": "+27 82 123 4567",
            "message": "Checking in from Open Chat Studio.",
        }
        data.update(overrides)
        return client.post(_whatsapp_url("whatsapp_send_test", provider), data=data)

    def test_sends_using_the_cached_phone_number_id(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "send_template_message") as send:
            response = self._post(authed_client, meta_provider)

        assert response.status_code == 200
        send.assert_called_once_with(
            message="Checking in from Open Chat Studio.",
            from_="1020671484465717",
            to="+27821234567",
            platform=ChannelPlatform.WHATSAPP,
        )
        assert "+27821234567" in response.content.decode()

    def test_shows_what_meta_said_when_it_rejects_the_message(self, meta_provider, authed_client):
        body = '{"error": {"message": "Template name does not exist", "code": 132001}}'
        error = httpx.HTTPStatusError(
            "bad request",
            request=httpx.Request("POST", "https://test"),
            response=httpx.Response(400, text=body, request=httpx.Request("POST", "https://test")),
        )
        with mock.patch.object(MetaCloudAPIService, "send_template_message", side_effect=error):
            response = self._post(authed_client, meta_provider)

        content = response.content.decode()
        assert response.status_code == 200
        assert "400" in content
        assert "132001" in content

    def test_shows_the_service_window_message(self, meta_provider, authed_client):
        error = ServiceWindowExpiredException("The 'new_bot_message' template was not found.")
        with mock.patch.object(MetaCloudAPIService, "send_template_message", side_effect=error):
            response = self._post(authed_client, meta_provider)

        assert "template was not found" in response.content.decode()

    def test_rejects_a_number_that_is_not_a_phone_number(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "send_template_message") as send:
            response = self._post(authed_client, meta_provider, to_number="nope")

        send.assert_not_called()
        assert "valid phone number" in response.content.decode()

    def test_rejects_a_sender_that_is_not_on_the_account(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "send_template_message") as send:
            response = self._post(authed_client, meta_provider, from_number_id="999")

        send.assert_not_called()
        assert response.status_code == 200


@pytest.mark.django_db()
class TestWhatsappTestFormAvailability:
    """The test send needs a usable template, so the form follows the cached check."""

    def test_form_is_enabled_when_the_template_is_usable(self, meta_provider, authed_client):
        _cache_template(meta_provider, ok=True, template={"status": "APPROVED"})

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.context["template_ok"] is True
        assert "Send test message" in response.content.decode()

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param({"problems": ["No template named 'new_bot_message'"]}, id="problems"),
            pytest.param({"error": "(#190) Error validating access token"}, id="api_error"),
            pytest.param(None, id="never_checked"),
        ],
    )
    def test_form_is_disabled_when_the_template_is_not_usable(self, meta_provider, authed_client, status):
        if status is not None:
            _cache_template(meta_provider, **status)

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        content = response.content.decode()
        assert response.context["template_ok"] is False
        assert re.search(r"<fieldset[^>]*\sdisabled", content)
        assert "Test messages can only be sent once the template above is available." in content

    def test_a_refresh_keeps_the_form_enabled(self, meta_provider, authed_client):
        """The refresh re-renders from the cache, so the form does not flicker back to disabled."""
        _cache_template(meta_provider, ok=True, template={"status": "APPROVED"})

        with mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay"):
            response = authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        assert response.context["template_ok"] is True
