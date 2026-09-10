"""Renewing a session token with the host's client-credentials token.

The session token is the visitor's credential and expires on its own clock. `POST chat/<id>/token/`
mints a replacement under the same admission as `chat/start/`: an `oauth`-mode channel, a
`chat:start` machine token for the session's chatbot, and the channel's origin rule -- so on a
browser-facing channel the widget calls it with a fresh token from the host, and on a server-only
channel the host's backend may. Every refusal looks the same.
"""

import uuid
from datetime import datetime, timedelta

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.api.session_tokens import issue_session_token
from apps.channels.models import ChannelPlatform, CredentialMode, WidgetAuthLevel
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

ORIGIN = "https://example.com"
DENIED = {"error": "Authentication required to chat with this chatbot", "code": "chat_access_denied"}


def _channel(experiment, mode=CredentialMode.OAUTH, allowed_domains=("example.com",), lifetime=None):
    return ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        credential_mode=mode,
        required_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        session_token_lifetime=lifetime,
        extra_data={"widget_token": "test_widget_token_123456789012", "allowed_domains": list(allowed_domains)},
    )


def _machine_client(team, allowed_chatbots=None, scopes=("chat:start",)):
    return ApiTestClient(
        UserFactory.create(),
        team,
        auth_method="oauth_client_credentials",
        scopes=list(scopes),
        allowed_chatbots=allowed_chatbots,
    )


def _renew(client, session_id, origin=ORIGIN, **headers):
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    return client.post(reverse("api:chat:renew-session-token", kwargs={"session_id": session_id}), **headers)


def _poll(session, token):
    url = reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})
    return APIClient().get(url, HTTP_X_SESSION_TOKEN=token)


@pytest.fixture()
def chatbot(db):
    return ExperimentFactory.create(team=TeamFactory.create())


@pytest.fixture()
def channel(chatbot):
    return _channel(chatbot)


@pytest.fixture()
def session(chatbot, channel):
    return ExperimentSessionFactory.create(experiment=chatbot, experiment_channel=channel)


@pytest.mark.django_db()
def test_machine_token_renews_an_expired_session_token(chatbot, session):
    old_token = issue_session_token(session)

    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        # Minted now: the host fetches a fresh machine token before each renewal.
        client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])
        assert _poll(session, old_token).status_code == 403

        response = _renew(client, session.external_id)

        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["session_id"] == str(session.external_id)
        assert body["session_token"] != old_token
        assert _poll(session, body["session_token"]).status_code == 200


@pytest.mark.django_db()
def test_renewed_token_expires_after_the_channel_lifetime(chatbot):
    channel = _channel(chatbot, lifetime=timedelta(hours=2))
    session = ExperimentSessionFactory.create(experiment=chatbot, experiment_channel=channel)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    with time_machine.travel(timezone.now(), tick=False):
        body = _renew(client, session.external_id).json()
        expected = (timezone.now() + timedelta(hours=2)).replace(microsecond=0)
        assert datetime.fromisoformat(body["expires_at"]) == expected


@pytest.mark.django_db()
def test_server_integration_needs_no_origin(chatbot):
    channel = _channel(chatbot, allowed_domains=())
    session = ExperimentSessionFactory.create(experiment=chatbot, experiment_channel=channel)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    assert _renew(client, session.external_id, origin=None).status_code == 200


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "origin",
    [
        pytest.param(None, id="originless"),
        pytest.param("https://evil.example", id="unlisted-origin"),
    ],
)
def test_browser_facing_channel_refuses_a_bad_origin(chatbot, session, origin):
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _renew(client, session.external_id, origin=origin)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_the_session_token_itself_does_not_renew(session):
    """The visitor's credential cannot mint its own replacement; only the host's can."""
    response = _renew(APIClient(), session.external_id, HTTP_X_SESSION_TOKEN=issue_session_token(session))

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "scopes",
    [
        pytest.param(("chatbots:interact",), id="broad-scope-without-chat-start"),
        pytest.param(("sessions:write",), id="unrelated-scope"),
    ],
)
def test_token_without_chat_start_scope_is_refused(chatbot, session, scopes):
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot], scopes=scopes)

    response = _renew(client, session.external_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_machine_token_for_another_team_is_refused(chatbot, session):
    client = _machine_client(TeamFactory.create(), allowed_chatbots=[chatbot])

    response = _renew(client, session.external_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_application_that_does_not_list_the_chatbot_is_refused(chatbot, session):
    other = ExperimentFactory.create(team=chatbot.team)
    client = _machine_client(chatbot.team, allowed_chatbots=[other])

    response = _renew(client, session.external_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_channel_in_embed_key_mode_is_refused(chatbot):
    channel = _channel(chatbot, mode=CredentialMode.EMBED_KEY)
    session = ExperimentSessionFactory.create(experiment=chatbot, experiment_channel=channel)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _renew(client, session.external_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_session_without_a_channel_is_refused(chatbot):
    session = ExperimentSessionFactory.create(experiment=chatbot, experiment_channel=None, platform=ChannelPlatform.WEB)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _renew(client, session.external_id)

    assert response.status_code == 401
    assert response.json() == DENIED


def _disable(channel):
    channel.enabled = False
    channel.save()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "switch_off",
    [
        pytest.param(lambda channel: channel.soft_delete(), id="deleted"),
        pytest.param(_disable, id="disabled"),
    ],
)
def test_channel_switched_off_after_the_session_started_is_refused(chatbot, channel, session, switch_off):
    """The channel is re-read at renewal, so the cached session's snapshot of it does not admit."""
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])
    assert _poll(session, issue_session_token(session)).status_code == 200  # primes the session cache

    switch_off(channel)
    response = _renew(client, session.external_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_renewed_token_takes_the_channel_lifetime_as_it_stands(chatbot, channel, session):
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])
    assert _poll(session, issue_session_token(session)).status_code == 200  # primes the session cache

    channel.session_token_lifetime = timedelta(minutes=15)
    channel.save()
    with time_machine.travel(timezone.now(), tick=False):
        body = _renew(client, session.external_id).json()
        expected = (timezone.now() + timedelta(minutes=15)).replace(microsecond=0)

    assert datetime.fromisoformat(body["expires_at"]) == expected


@pytest.mark.django_db()
def test_unknown_session_is_refused_not_404(chatbot):
    """Uniform with every other refusal: a token holder must not learn which session ids exist."""
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _renew(client, uuid.uuid4())

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_session_on_a_versioned_chatbot_is_admitted_through_the_working_version(chatbot, channel):
    """The allowlist holds working versions; a session bound to a published version still renews."""
    version = chatbot.create_new_version()
    session = ExperimentSessionFactory.create(experiment=version, experiment_channel=channel)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    assert _renew(client, session.external_id).status_code == 200
