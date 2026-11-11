"""Tests for the typed Chat API admission boundary."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from rest_framework.authentication import SessionAuthentication

from apps.api.admission import (
    ChatAdmissionError,
    ChatCapability,
    ChatCredentialKind,
    ChatEntryPoint,
    SessionTokenPolicy,
    chat_entry_point_for_request,
    get_chat_admission,
    resolve_chat_admission,
)
from apps.api.authentication import ChatOAuthAuthentication, EmbeddedWidgetAuthentication
from apps.api.models import UserAPIKey
from apps.channels.models import ChannelPlatform, CredentialMode, WidgetAuthLevel
from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


def _request(*, auth=None, user=None, authenticator=None, session_id=None):
    """Build a request with only the already-resolved authentication facts under test."""
    request = RequestFactory().get("/api/chat/")
    request.auth = auth
    request.user = user or AnonymousUser()
    request.successful_authenticator = authenticator
    request.parser_context = {"kwargs": {"session_id": session_id} if session_id else {}}
    return request


@pytest.fixture()
def channel(db):
    """Create an OAuth-mode Chat API Channel with the strict widget policy."""
    experiment = ExperimentFactory.create(team=TeamFactory.create())
    return ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        credential_mode=CredentialMode.OAUTH,
        required_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        extra_data={"allowed_domains": []},
    )


def test_anonymous_entry_point_is_explicit_and_fail_closed():
    """The current keyless start path is represented without inventing a channel or principal."""
    admission = resolve_chat_admission(
        _request(),
        entry_point=ChatEntryPoint.START_SESSION,
    )

    assert admission.credential_kind == ChatCredentialKind.ANONYMOUS
    assert admission.channel is None
    assert admission.principal is None
    assert admission.team is None
    assert admission.throttle_bucket[0] == "ip"
    assert admission.capabilities == frozenset({ChatCapability.START_SESSION})
    assert admission.session_token_policy == SessionTokenPolicy.NOT_APPLICABLE


@pytest.mark.django_db()
def test_embed_key_admission_uses_the_authenticated_channel(channel):
    """Embed Key resolution records the channel and its durable token policy."""
    request = _request(auth=channel, authenticator=EmbeddedWidgetAuthentication())

    admission = resolve_chat_admission(request, entry_point=ChatEntryPoint.START_SESSION)

    assert admission.credential_kind == ChatCredentialKind.EMBED_KEY
    assert admission.channel == channel
    assert admission.team == channel.team
    assert admission.throttle_bucket == ("channel", str(channel.pk))
    assert admission.session_token_policy == SessionTokenPolicy.ISSUE_AND_REQUIRE
    assert ChatCapability.ISSUE_SESSION_TOKEN in admission.capabilities


@pytest.mark.django_db()
def test_oauth_admission_keeps_the_channel_attribution(channel):
    """OAuth session start remains attributed to its validated Chat API Channel."""
    application = OAuth2Application.objects.create(
        name="machine-app",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
        team=channel.team,
    )
    token = OAuth2AccessToken(application=application, application_id=application.pk, team=channel.team)
    request = _request(auth=channel, authenticator=ChatOAuthAuthentication())
    request.chat_oauth_token = token

    admission = resolve_chat_admission(request, entry_point=ChatEntryPoint.START_SESSION)

    assert admission.credential_kind == ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS
    assert admission.channel == channel
    assert admission.principal == application
    assert admission.team == channel.team
    assert admission.throttle_bucket == ("channel", str(channel.pk))


@pytest.mark.django_db()
def test_validated_session_token_is_not_reread_from_headers(channel):
    """A session-token admission requires explicit prior validation and never parses a header."""
    session = SimpleNamespace(experiment_channel=channel, team=channel.team, external_id="session-123")
    request = _request(session_id="session-123")
    request.headers = {"X-Session-Token": "not-read-by-the-resolver"}

    admission = resolve_chat_admission(
        request,
        entry_point=ChatEntryPoint.CONTINUE_SESSION,
        session=session,
        session_token_validated=True,
    )

    assert admission.credential_kind == ChatCredentialKind.SESSION_TOKEN
    assert admission.throttle_bucket == ("session", "session-123")
    assert admission.capabilities == frozenset({ChatCapability.CONTINUE_SESSION})


@pytest.mark.django_db()
def test_client_credentials_token_is_classified_without_authenticating_again(channel):
    """A validated token object is classified directly rather than sent through OAuth again."""
    application = OAuth2Application.objects.create(
        name="machine-app",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
        team=channel.team,
    )
    token = OAuth2AccessToken(application=application, application_id=application.pk, team=channel.team)
    session = SimpleNamespace(experiment_channel=channel)
    request = _request(auth=token, session_id="session-456")

    admission = resolve_chat_admission(
        request,
        entry_point=ChatEntryPoint.RENEW_SESSION_TOKEN,
        session=session,
    )

    assert admission.credential_kind == ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS
    assert admission.principal == application
    assert admission.throttle_bucket == ("oauth_client", str(application.pk))


@pytest.mark.django_db()
def test_api_key_and_authorization_code_are_distinct_credentials(channel):
    """Other validated API credentials retain their own identity instead of becoming anonymous."""
    user = UserFactory.create()
    api_key, _ = UserAPIKey.objects.create_key(name="test key", user=user, team=channel.team)
    api_key_admission = resolve_chat_admission(
        _request(auth=api_key),
        entry_point=ChatEntryPoint.CONTINUE_SESSION,
    )

    application = OAuth2Application.objects.create(
        name="user-app",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
        team=channel.team,
    )
    oauth_user = UserFactory.create(username="oauth-user")
    token = OAuth2AccessToken(
        application=application,
        application_id=application.pk,
        user=oauth_user,
        team=channel.team,
    )
    oauth_admission = resolve_chat_admission(
        _request(auth=token),
        entry_point=ChatEntryPoint.CONTINUE_SESSION,
    )

    assert api_key_admission.credential_kind == ChatCredentialKind.API_KEY
    assert oauth_admission.credential_kind == ChatCredentialKind.OAUTH_AUTHORIZATION_CODE
    assert oauth_admission.principal == oauth_user


def test_an_unmarked_channel_authentication_is_rejected():
    """A channel-shaped auth value without an adapter marker is ambiguous and fails closed."""
    request = _request(auth=SimpleNamespace(), authenticator=SessionAuthentication())

    with pytest.raises(ChatAdmissionError, match="Unsupported or ambiguous"):
        resolve_chat_admission(request, entry_point=ChatEntryPoint.START_SESSION)


@pytest.mark.django_db()
def test_validated_embed_key_evidence_represents_a_cookie_authenticated_request(channel):
    """A key validated after another authenticator still becomes the admission credential."""
    admission = resolve_chat_admission(
        _request(),
        entry_point=ChatEntryPoint.START_SESSION,
        validated_embed_key_channel=channel,
    )

    assert admission.credential_kind == ChatCredentialKind.EMBED_KEY
    assert admission.channel == channel
    assert admission.team == channel.team


def test_get_chat_admission_reuses_the_request_result():
    """Adapters share one admission result instead of reconstructing credential identity."""
    request = _request()

    first = get_chat_admission(request, entry_point=ChatEntryPoint.START_SESSION)
    second = get_chat_admission(request, entry_point=ChatEntryPoint.START_SESSION)

    assert first is second


@pytest.mark.django_db()
def test_get_chat_admission_refreshes_for_a_session_context(channel):
    """A session-bound call cannot reuse an admission resolved without its session."""
    application = OAuth2Application.objects.create(
        name="machine-app",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
        team=channel.team,
    )
    token = OAuth2AccessToken(application=application, application_id=application.pk, team=channel.team)
    request = _request(auth=token)
    session = SimpleNamespace(experiment_channel=channel, team=channel.team, external_id="session-123")

    first = get_chat_admission(request, entry_point=ChatEntryPoint.CONTINUE_SESSION)
    second = get_chat_admission(request, entry_point=ChatEntryPoint.CONTINUE_SESSION, session=session)

    assert first is not second
    assert first.channel is None
    assert second.channel == channel
    assert second.session_token_policy == SessionTokenPolicy.ISSUE_AND_REQUIRE


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("/api/chat/start/", ChatEntryPoint.START_SESSION, id="start"),
        pytest.param("/api/chat/session/token/", ChatEntryPoint.RENEW_SESSION_TOKEN, id="renew"),
        pytest.param("/api/chat/session/poll/", ChatEntryPoint.CONTINUE_SESSION, id="continue"),
    ],
)
def test_chat_entry_point_matches_the_chat_api_route(path, expected):
    """Chat routes map to the same operation families used by the admission resolver."""
    request = RequestFactory().get(path)

    assert chat_entry_point_for_request(request) == expected
