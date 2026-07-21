"""Tests for the durable per-channel widget auth policy (issue #3858).

Covers the three WidgetAuthLevel levels: how `chat_start_session` issues (or opts out
of) a session token per level, and how `SessionAccessPermission` enforces each level on
subsequent requests.
"""

import importlib

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.session_tokens import issue_session_token
from apps.channels.models import ChannelPlatform, WidgetAuthLevel
from apps.experiments.models import ExperimentSession
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory

WIDGET_TOKEN = "test_widget_token_123456789012"


@pytest.fixture()
def api_client():
    return APIClient()


def _widget_channel(experiment, level):
    return ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        required_auth_level=level,
        extra_data={"widget_token": WIDGET_TOKEN, "allowed_domains": ["example.com"]},
    )


def _start(api_client, experiment, **extra):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "session_data": {"source": "widget"}}
    return api_client.post(url, data=data, format="json", **extra)


def poll_url(session):
    return reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})


# --- defaults -------------------------------------------------------------------------


@pytest.mark.django_db()
def test_new_widget_channel_defaults_to_session_token(experiment):
    channel = ExperimentChannelFactory.create(experiment=experiment, platform=ChannelPlatform.EMBEDDED_WIDGET)
    assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN
    assert channel.widget_auth_level == WidgetAuthLevel.SESSION_TOKEN


@pytest.mark.django_db()
def test_non_widget_channel_has_no_auth_level(experiment):
    channel = ExperimentChannelFactory.create(experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    assert channel.widget_auth_level is None


# --- start session per level ----------------------------------------------------------


@pytest.mark.django_db()
def test_session_token_level_issues_and_enforces_token(api_client, experiment):
    channel = _widget_channel(experiment, WidgetAuthLevel.SESSION_TOKEN)
    response = _start(api_client, experiment, HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["session_token"]
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is True
    assert session.experiment_channel_id == channel.id

    # token-less request rejected with a clear error
    denied = api_client.get(poll_url(session), HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://example.com")
    assert denied.status_code == 403
    assert denied.json()["code"] == "session_token_required"

    # the issued token grants access
    allowed = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=body["session_token"])
    assert allowed.status_code == 200


@pytest.mark.django_db()
def test_embed_key_level_opts_out_but_requires_embed_key(api_client, experiment):
    _widget_channel(experiment, WidgetAuthLevel.EMBED_KEY)
    response = _start(api_client, experiment, HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["session_token"] is None
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is False

    # a valid embed key + domain grants access, no session token needed
    allowed = api_client.get(poll_url(session), HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://example.com")
    assert allowed.status_code == 200

    # but without the embed key the public/allowlist fallback is blocked
    denied = api_client.get(poll_url(session))
    assert denied.status_code == 403


@pytest.mark.django_db()
def test_none_level_allows_legacy_public_access(api_client, experiment):
    _widget_channel(experiment, WidgetAuthLevel.NONE)
    response = _start(api_client, experiment, HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["session_token"] is None
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is False

    # legacy: a public experiment is reachable without any embed key or token
    assert api_client.get(poll_url(session)).status_code == 200


@pytest.mark.django_db()
def test_none_level_still_blocks_non_allowlisted_participant(api_client, experiment):
    experiment.participant_allowlist = ["someone@example.com"]
    experiment.save(update_fields=["participant_allowlist"])
    channel = _widget_channel(experiment, WidgetAuthLevel.NONE)
    session = ExperimentSessionFactory.create(
        experiment=experiment, experiment_channel=channel, session_token_required=False
    )
    assert api_client.get(poll_url(session)).status_code == 403


# --- permission enforcement independent of start ---------------------------------------


@pytest.mark.django_db()
def test_embed_key_level_blocks_public_fallback_without_key(api_client, experiment):
    """Even for a public experiment, an EMBED_KEY widget channel needs the embed key."""
    channel = _widget_channel(experiment, WidgetAuthLevel.EMBED_KEY)
    session = ExperimentSessionFactory.create(
        experiment=experiment, experiment_channel=channel, session_token_required=False
    )
    assert experiment.is_public
    assert api_client.get(poll_url(session)).status_code == 403


@pytest.mark.django_db()
def test_session_token_level_rejects_embed_key_only(api_client, experiment):
    channel = _widget_channel(experiment, WidgetAuthLevel.SESSION_TOKEN)
    session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)
    # embed key alone does not satisfy a session-token channel
    denied = api_client.get(poll_url(session), HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://example.com")
    assert denied.status_code == 403
    # with the token it works
    allowed = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=issue_session_token(session))
    assert allowed.status_code == 200


@pytest.mark.django_db()
def test_session_token_level_embed_key_cannot_bypass_opted_out_session(api_client, experiment):
    """Defense in depth: even a session mis-configured with session_token_required=False on a
    SESSION_TOKEN channel must not be reachable with an embed key alone — the level gates the
    legacy embed-key path."""
    channel = _widget_channel(experiment, WidgetAuthLevel.SESSION_TOKEN)
    session = ExperimentSessionFactory.create(
        experiment=experiment, experiment_channel=channel, session_token_required=False
    )
    denied = api_client.get(poll_url(session), HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://example.com")
    assert denied.status_code == 403


@pytest.mark.django_db()
def test_embed_key_for_other_channel_denied(api_client, experiment):
    """Cross-channel isolation: a valid embed key for a *different* widget channel of the
    same experiment must not grant access to this session, even at EMBED_KEY level."""
    session_channel = ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        extra_data={"widget_token": "session_channel_token_0000001", "allowed_domains": ["b.example.com"]},
    )
    ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        extra_data={"widget_token": "attacker_channel_token_000001", "allowed_domains": ["a.example.com"]},
    )
    session = ExperimentSessionFactory.create(
        experiment=experiment, experiment_channel=session_channel, session_token_required=False
    )
    # The attacker sends their own valid embed key + their own allowed origin.
    denied = api_client.get(
        poll_url(session),
        HTTP_X_EMBED_KEY="attacker_channel_token_000001",
        HTTP_ORIGIN="https://a.example.com",
    )
    assert denied.status_code == 403
    # the session's own channel key still works
    allowed = api_client.get(
        poll_url(session),
        HTTP_X_EMBED_KEY="session_channel_token_0000001",
        HTTP_ORIGIN="https://b.example.com",
    )
    assert allowed.status_code == 200


# --- migration version → level mapping -------------------------------------------------

_migration = importlib.import_module("apps.channels.migrations.0029_experimentchannel_required_auth_level")


@pytest.mark.parametrize(
    ("widget_version", "expected"),
    [
        pytest.param("unknown", _migration.LEVEL_NONE, id="unknown-placeholder"),
        pytest.param("0.4.8", _migration.LEVEL_NONE, id="pre-0.5.1"),
        pytest.param("0.5.0", _migration.LEVEL_NONE, id="just-below-embed-key"),
        pytest.param("0.5.1", _migration.LEVEL_EMBED_KEY, id="embed-key-floor"),
        pytest.param("0.8.9", _migration.LEVEL_EMBED_KEY, id="embed-key-ceiling"),
        pytest.param("0.9.0", _migration.LEVEL_SESSION_TOKEN, id="session-token-floor"),
        pytest.param("0.10.0", _migration.LEVEL_SESSION_TOKEN, id="session-token-above"),
        pytest.param("garbage", _migration.LEVEL_NONE, id="unparseable"),
        pytest.param(None, _migration.LEVEL_SESSION_TOKEN, id="never-connected"),
    ],
)
def test_migration_level_for_version(widget_version, expected):
    assert _migration._level_for_version(widget_version) == expected
