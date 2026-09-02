import re
from datetime import timedelta
from unittest import mock

import httpx
import pytest
from django.contrib import messages as django_messages
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.exceptions import ServiceWindowExpiredException
from apps.service_providers.exceptions import (
    ConnectionTestNotSupportedError,
    NoTestableModelError,
    ServiceProviderConfigError,
)
from apps.service_providers.messaging_service import MetaCloudAPIService
from apps.service_providers.models import (
    AuthProvider,
    LlmProvider,
    LlmProviderTypes,
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
    LlmProviderModelFactory,
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


@pytest.mark.django_db()
def test_llm_provider_create_view_shows_create_and_test_button(team_with_users, authed_client):
    """The create-page button says up front that saving will also test credentials - static
    text, no Alpine needed, since a fresh provider has no prior config to react to."""
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "subtype": "openai"},
        )
    )
    content = response.content.decode()
    assert "Create and Test" in content
    assert 'x-text="configChanged' not in content


@pytest.mark.django_db()
def test_voice_provider_create_view_shows_plain_create_button(team_with_users, authed_client):
    """Regression: only LLM providers get the "and Test" wording - every other provider
    type's create button must render exactly as it did before this feature existed."""
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "subtype": "aws"},
        )
    )
    content = response.content.decode()
    assert "Create and Test" not in content
    assert "and Test" not in content


@pytest.mark.django_db()
def test_llm_provider_edit_view_shows_reactive_update_button(team_with_users, authed_client):
    """The edit-page button must be the Alpine-reactive one (default text "Update", swaps
    to "Update and Test" once a credential field changes), not the static create-page one."""
    provider = LlmProviderFactory(team=team_with_users)
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )
    content = response.content.decode()
    assert "x-text=\"configChanged ? 'Update and Test' : 'Update'\"" in content
    assert ">Update</button>" in content


@pytest.mark.django_db()
def test_llm_provider_edit_view_test_connection_form_hides_on_config_change(team_with_users, authed_client):
    """The manual Test Connection form must be wired to hide the moment credentials change -
    otherwise it can be clicked against stale, unsaved data."""
    provider = LlmProviderFactory(team=team_with_users)
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )
    content = response.content.decode()
    assert 'x-show="!configChanged"' in content


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
def test_test_llm_connection_endpoint_success(team_with_users, authed_client):
    """POST to the test-connection endpoint should call test_connection on the provider and report success."""
    provider = LlmProviderFactory(team=team_with_users)
    url = reverse(
        "service_providers:test_llm_connection",
        kwargs={
            "team_slug": team_with_users.slug,
            "provider_type": "llm",
            "pk": provider.pk,
        },
    )
    with mock.patch.object(LlmProvider, "test_connection") as mock_test:
        response = authed_client.post(url)

    assert response.status_code == 302
    mock_test.assert_called_once()


def _test_connection_url(team, provider):
    return reverse(
        "service_providers:test_llm_connection",
        kwargs={"team_slug": team.slug, "provider_type": "llm", "pk": provider.pk},
    )


class _FakeTransientError(Exception):
    """Stands in for a provider SDK error carrying an HTTP status code, without needing
    to construct a real openai/anthropic exception in the test."""

    status_code = 429


@pytest.mark.django_db()
def test_test_llm_connection_endpoint_no_models_configured(team_with_users, authed_client):
    """No LlmProviderModel for the provider's type should produce a warning, not a crash."""
    provider = LlmProviderFactory(team=team_with_users)
    url = _test_connection_url(team_with_users, provider)

    with mock.patch.object(LlmProvider, "test_connection", side_effect=NoTestableModelError(provider.type)):
        response = authed_client.post(url)

    assert response.status_code == 302
    [msg] = list(get_messages(response.wsgi_request))
    assert msg.level == django_messages.WARNING


@pytest.mark.django_db()
def test_test_llm_connection_endpoint_unsupported_provider(team_with_users, authed_client):
    """A provider type that can't do chat completions (e.g. Voyage AI) should say so, not error out."""
    provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.voyage))
    url = _test_connection_url(team_with_users, provider)

    with mock.patch.object(LlmProvider, "test_connection", side_effect=ConnectionTestNotSupportedError(provider.type)):
        response = authed_client.post(url)

    assert response.status_code == 302
    [msg] = list(get_messages(response.wsgi_request))
    assert msg.level == django_messages.INFO


@pytest.mark.django_db()
def test_test_llm_connection_endpoint_invalid_configuration(team_with_users, authed_client):
    """A genuinely invalid configuration is not the same as an unsupported provider type, and
    must not be reported as one (it's a real, actionable problem). It never reaches the
    provider at all, so it's classified the same as a rejected credential."""
    provider = LlmProviderFactory(team=team_with_users)
    url = _test_connection_url(team_with_users, provider)

    with mock.patch.object(
        LlmProvider, "test_connection", side_effect=ServiceProviderConfigError(provider.type, "bad config")
    ):
        response = authed_client.post(url)

    assert response.status_code == 302
    [msg] = list(get_messages(response.wsgi_request))
    assert msg.level == django_messages.ERROR
    assert "credentials" in msg.message.lower()


@pytest.mark.django_db()
def test_test_llm_connection_endpoint_transient_failure(team_with_users, authed_client):
    """A rate-limit/timeout style failure should be reported as temporary, not a credentials problem."""
    provider = LlmProviderFactory(team=team_with_users)
    url = _test_connection_url(team_with_users, provider)

    with mock.patch.object(LlmProvider, "test_connection", side_effect=_FakeTransientError()):
        response = authed_client.post(url)

    assert response.status_code == 302
    [msg] = list(get_messages(response.wsgi_request))
    assert msg.level == django_messages.WARNING


@pytest.mark.django_db()
def test_test_llm_connection_endpoint_timeout_is_temporary_not_credential_failure(team_with_users, authed_client):
    """A timeout isn't in RATE_LIMIT_EXCEPTIONS or carrying a 429/503 status code, so
    should_retry_exception alone misses it. It must still be reported as temporary, not
    mistaken for bad credentials."""
    import openai  # noqa: PLC0415 - heavy lib, slow startup

    provider = LlmProviderFactory(team=team_with_users)
    url = _test_connection_url(team_with_users, provider)
    timeout_error = openai.APITimeoutError(httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))

    with mock.patch.object(LlmProvider, "test_connection", side_effect=timeout_error):
        response = authed_client.post(url)

    assert response.status_code == 302
    [msg] = list(get_messages(response.wsgi_request))
    assert msg.level == django_messages.WARNING


@pytest.mark.django_db()
def test_test_llm_connection_endpoint_credential_failure(team_with_users, authed_client):
    """A rejected credential (a 4xx-range status code, same shape a real SDK exception has -
    checked directly against openai.APIStatusError/anthropic.APIStatusError) should be
    reported as an error, not a warning, and should point at credentials specifically."""
    provider = LlmProviderFactory(team=team_with_users)
    url = _test_connection_url(team_with_users, provider)
    invalid_api_key_error = ValueError("invalid api key")
    invalid_api_key_error.status_code = 401

    with mock.patch.object(LlmProvider, "test_connection", side_effect=invalid_api_key_error):
        response = authed_client.post(url)

    assert response.status_code == 302
    [msg] = list(get_messages(response.wsgi_request))
    assert msg.level == django_messages.ERROR
    assert "credentials" in msg.message.lower()


@pytest.mark.django_db()
def test_test_llm_connection_endpoint_connection_issue_failure(team_with_users, authed_client):
    """A provider-side failure (a 5xx-range status code, or a raw exception with no status
    code at all - e.g. a network-level connection error) is a different, actionable problem
    from a rejected credential, and must be reported as one, not lumped in with it."""
    provider = LlmProviderFactory(team=team_with_users)
    url = _test_connection_url(team_with_users, provider)
    server_error = ValueError("upstream is down")
    server_error.status_code = 502

    with mock.patch.object(LlmProvider, "test_connection", side_effect=server_error):
        response = authed_client.post(url)

    assert response.status_code == 302
    [msg] = list(get_messages(response.wsgi_request))
    assert msg.level == django_messages.ERROR
    assert "credentials" not in msg.message.lower()
    assert "provider's side" in msg.message


@pytest.mark.django_db()
def test_updating_llm_provider_runs_automatic_connection_test(team_with_users, authed_client):
    """Saving an LlmProvider through the real edit view should trigger the automatic
    post-save connection test and surface its warning on failure, without blocking the save.

    Regression: the save must redirect back to this same edit page, not the team list -
    the warning explicitly says "Use Test Connection below to retry", and that button only
    exists on the edit page. A save that lands anywhere else makes that instruction wrong."""
    provider = LlmProviderFactory(team=team_with_users, name="Old Name")
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
    )

    with mock.patch.object(LlmProvider, "test_connection", side_effect=RuntimeError("boom")):
        response = authed_client.post(url, data={"name": "New Name", "openai_api_key": "new-key"}, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(url, 302)]
    messages_seen = [str(m) for m in response.context["messages"]]
    assert any("connection test failed" in m.lower() for m in messages_seen)

    provider.refresh_from_db()
    assert provider.name == "New Name"


@pytest.mark.django_db()
def test_updating_llm_provider_with_passing_test_redirects_to_team_list(team_with_users, authed_client):
    """No connection-test warning means no reason to detour through the edit page - a
    successful save behaves exactly like every other provider save and returns to the
    team list. (Voyage AI, which skips the test entirely, behaves the same way; a missing
    model does not - see test_updating_llm_provider_with_no_model_configured below.)"""
    provider = LlmProviderFactory(team=team_with_users, name="Old Name")
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
    )
    team_list_url = reverse("single_team:manage_team", kwargs={"team_slug": team_with_users.slug})

    with mock.patch.object(LlmProvider, "test_connection"):
        response = authed_client.post(url, data={"name": "New Name", "openai_api_key": "new-key"}, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(team_list_url, 302)]


@pytest.mark.django_db()
def test_updating_llm_provider_with_no_model_configured(team_with_users, authed_client):
    """A provider with no configured model to test against is genuinely unverified, not
    silently-fine setup state - it should warn (redirecting back to the edit page, same as
    a real failure) instead of behaving like a passing test."""
    provider = LlmProviderFactory(team=team_with_users, name="Old Name")
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
    )

    with mock.patch.object(LlmProvider, "test_connection", side_effect=NoTestableModelError(provider.type)):
        response = authed_client.post(url, data={"name": "New Name", "openai_api_key": "new-key"}, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(url, 302)]
    messages_seen = [str(m) for m in response.context["messages"]]
    assert any("no models configured" in m.lower() for m in messages_seen)


@pytest.mark.django_db()
def test_updating_llm_provider_with_no_credential_change_skips_connection_test(team_with_users, authed_client):
    """Editing an unrelated field (name) and re-submitting the same credentials must not
    re-run the connection test - that's a real external call, not free, and nothing about
    the credentials is actually in question."""
    provider = LlmProviderFactory(team=team_with_users, name="Old Name")
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
    )
    team_list_url = reverse("single_team:manage_team", kwargs={"team_slug": team_with_users.slug})

    with mock.patch.object(LlmProvider, "test_connection") as mock_test:
        response = authed_client.post(
            url,
            # dict(...) here only works around a ty false positive on factory.Dict-declared
            # fields; provider.config is a plain dict at runtime either way.
            data={"name": "New Name", "openai_api_key": dict(provider.config)["openai_api_key"]},
            follow=True,
        )

    mock_test.assert_not_called()
    assert response.status_code == 200
    assert response.redirect_chain == [(team_list_url, 302)]
    provider.refresh_from_db()
    assert provider.name == "New Name"


@pytest.mark.django_db()
def test_updating_llm_provider_with_credential_change_runs_connection_test(team_with_users, authed_client):
    """The counterpart to the skip case above: an actual credential change must still
    trigger the test - the gating is on whether config changed, not a blanket skip."""
    provider = LlmProviderFactory(team=team_with_users, name="Old Name")
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
    )

    with mock.patch.object(LlmProvider, "test_connection") as mock_test:
        authed_client.post(url, data={"name": "Old Name", "openai_api_key": "a-genuinely-new-key"}, follow=True)

    mock_test.assert_called_once()


@pytest.mark.django_db()
def test_creating_llm_provider_runs_automatic_connection_test(team_with_users, authed_client):
    """A brand-new provider has no "previous config" to compare against, so creation always
    tests - there's nothing to gate on."""
    url = reverse(
        "service_providers:new",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "subtype": "openai"},
    )

    with mock.patch.object(LlmProvider, "test_connection") as mock_test:
        authed_client.post(url, data={"name": "New Provider", "openai_api_key": "brand-new-key"}, follow=True)

    mock_test.assert_called_once()


@pytest.mark.django_db()
def test_updating_voice_provider_still_redirects_to_team_list(team_with_users, authed_client):
    """Regression: the edit-page redirect only applies when an LLM connection-test warning
    actually fired. Every other provider type must keep today's behavior unchanged."""
    provider = VoiceProviderFactory(team=team_with_users, name="Old Name")
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "pk": provider.pk},
    )
    team_list_url = reverse("single_team:manage_team", kwargs={"team_slug": team_with_users.slug})

    response = authed_client.post(
        url,
        data={
            "name": "New Name",
            "aws_access_key_id": "new-id",
            "aws_secret_access_key": "new-secret",
            "aws_region": "us-east-1",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert response.redirect_chain == [(team_list_url, 302)]


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
def test_create_view_shows_empty_state_for_provider_with_no_default_models(team_with_users, authed_client):
    """LiteLLM ships no default models (every backend is install-specific, same as OpenRouter).

    The default-models section must say so rather than rendering nothing, which is
    indistinguishable from a broken page.
    """
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "subtype": "litellm"},
        )
    )
    assert response.status_code == 200
    assert b"No default models are available for this provider" in response.content


@pytest.mark.django_db()
def test_create_view_still_shows_default_models_for_openai(team_with_users, authed_client):
    """Regression guard: the empty-state message must not appear for a provider that does
    ship default models.

    Creates its own global model row rather than relying on the migration-seeded ones -
    see the "migration-seeded global rows" invariant in AGENTS.md.
    """
    LlmProviderModelFactory(team=None, type=str(LlmProviderTypes.openai), name="test-default-model")
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "subtype": "openai"},
        )
    )
    assert response.status_code == 200
    assert b"No default models are available for this provider" not in response.content
    assert b"test-default-model" in response.content


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
        meta_provider.mark_whatsapp_refresh_queued()

        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert _whatsapp_url("whatsapp_status", meta_provider) in content
        assert 'hx-trigger="every 2s"' in content
        assert "Checking with Meta" in content

    def test_keeps_polling_once_the_numbers_land_but_the_template_check_has_not(self, meta_provider, authed_client):
        """The two legs commit separately, and the swap replaces the polling element itself.

        A poll answered between them must still carry the trigger, or polling dies for good
        and the panel is stuck on "Never checked" until someone hits Refresh.
        """
        meta_provider.mark_whatsapp_refresh_queued()
        with mock.patch.object(MetaCloudAPIService, "get_phone_numbers", return_value=[]):
            meta_provider.sync_whatsapp_numbers()

        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert 'hx-trigger="every 2s"' in content
        assert "Checking with Meta" in content

    def test_stops_polling_once_the_whole_refresh_is_done(self, meta_provider, authed_client):
        meta_provider.mark_whatsapp_refresh_queued()
        meta_provider.mark_whatsapp_refresh_done()

        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert 'hx-trigger="every 2s"' not in content

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
        assert meta_provider.whatsapp_refresh_info["started_at"]

    def test_does_not_queue_a_second_refresh_while_one_is_running(
        self, meta_provider, authed_client, django_capture_on_commit_callbacks
    ):
        meta_provider.mark_whatsapp_refresh_queued()

        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        delay.assert_not_called()

    def test_queues_a_refresh_when_the_running_one_has_stalled(
        self, meta_provider, authed_client, django_capture_on_commit_callbacks
    ):
        stalled = timezone.now() - timedelta(minutes=30)
        meta_provider.extra_data["whatsapp_refresh"] = {"started_at": stalled.isoformat()}
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
