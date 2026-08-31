from unittest import mock

import httpx
import pytest
from django.contrib import messages as django_messages
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.service_providers.exceptions import (
    ConnectionTestNotSupportedError,
    NoTestableModelError,
    ServiceProviderConfigError,
)
from apps.service_providers.models import (
    AuthProvider,
    LlmProvider,
    LlmProviderTypes,
    MessagingProvider,
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
    successful (or silently-skipped, e.g. no model configured) save behaves exactly like
    every other provider save and returns to the team list."""
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
