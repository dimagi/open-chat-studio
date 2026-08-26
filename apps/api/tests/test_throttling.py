"""Tests for the api-scope DRF throttle (issues #2349 / #2140)."""

import json
from datetime import timedelta
from unittest import mock

import pytest
from django.conf import settings
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.throttling import SimpleRateThrottle
from waffle import get_waffle_flag_model

from apps.api.models import UserAPIKey
from apps.api.throttling import APIRateThrottle, ChatAPIRateThrottle
from apps.channels.models import ChannelPlatform, CredentialMode, WidgetAuthLevel
from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.rate_limit import RATE_LIMIT_EXEMPT_FLAG, check
from apps.utils.tests.clients import ApiTestClient

TINY_LIMITS = settings.RATE_LIMITS | {"api": {"rate": "3/5m", "fail_open": True}}


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()  # Clear all waffle and other caches


@pytest.fixture()
def experiment(db):
    return ExperimentFactory.create(team=TeamWithUsersFactory.create())


def _view_stub(**kwargs):
    """A stand-in for the DRF view, which throttles read URL captures from."""
    return type("View", (), {"kwargs": kwargs})()


def test_throttle_does_not_use_drf_history_storage():
    """Counting goes through the shared core, not SimpleRateThrottle."""
    assert not issubclass(APIRateThrottle, SimpleRateThrottle)


def test_identity_prefers_team_then_falls_through(db):
    """Team > api key > user > ip, exactly one bucket per request."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    throttle = APIRateThrottle()

    request = RequestFactory().get("/")
    request.team = team
    assert throttle.identity(request, _view_stub()) == ("team", str(team.pk))

    request = RequestFactory().get("/")
    request.user = user
    assert throttle.identity(request, _view_stub()) == ("user", str(user.pk))

    request = RequestFactory().get("/")
    assert throttle.identity(request, _view_stub())[0] == "ip"


@pytest.mark.django_db()
def test_identity_falls_through_to_api_key():
    """No resolvable team, but request.auth is a UserAPIKey: keys by the key's pk."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    api_key, _ = UserAPIKey.objects.create_key(name="test key", user=user, team=team, read_only=False)
    throttle = APIRateThrottle()

    request = RequestFactory().get("/")
    request.auth = api_key

    assert throttle.identity(request, _view_stub()) == ("api_key", str(api_key.pk))


@pytest.mark.django_db()
def test_identity_falls_through_to_oauth_client():
    """No resolvable team, but request.auth is an OAuth2AccessToken: keys by the token's application."""
    team = TeamWithUsersFactory.create()
    application = OAuth2Application.objects.create(
        name="machine-app",
        client_id="machine-client-id",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
        team=team,
    )
    access_token = OAuth2AccessToken.objects.create(
        application=application,
        team=team,
        token="machine-token",
        scope="sessions:read",
        expires=timezone.now() + timedelta(days=1),
    )
    throttle = APIRateThrottle()

    request = RequestFactory().get("/")
    request.auth = access_token

    assert throttle.identity(request, _view_stub()) == ("oauth_client", str(access_token.application_id))


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
@pytest.mark.django_db()
def test_api_request_under_limit_carries_headers(experiment):
    """Success responses carry X-RateLimit-Limit/Remaining/Reset."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "3"
    assert response.headers["X-RateLimit-Remaining"] == "2"
    assert int(response.headers["X-RateLimit-Reset"]) > 0


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
@pytest.mark.django_db()
def test_api_request_over_limit_gets_429_contract(experiment):
    """Over the limit, the API returns 429 with Retry-After and the pinned body; the view never runs."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    for _ in range(3):
        client.get(reverse("api:experiment-list"))
    response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    body = json.loads(response.content)
    assert body == {"detail": "Rate limit exceeded.", "available_in": body["available_in"]}
    assert body["available_in"] > 0


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
@pytest.mark.django_db()
def test_api_over_limit_logs_would_block_in_log_only_mode(experiment, caplog):
    """Log-only mode serves the request and logs one would_block event."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    for _ in range(3):
        client.get(reverse("api:experiment-list"))
    with caplog.at_level("INFO", logger="ocs.rate_limit"):
        response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 200
    would_block = [r for r in caplog.records if r.message == "rate_limit.would_block"]
    assert len(would_block) == 1
    assert would_block[0].team_id == experiment.team.pk


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
@pytest.mark.django_db()
def test_exempt_team_is_never_blocked(experiment):
    """The flag_ignore_rate_limiting flag bypasses enforcement."""
    flag, _ = get_waffle_flag_model().objects.update_or_create(
        name=RATE_LIMIT_EXEMPT_FLAG, defaults={"everyone": False}
    )
    flag.teams.add(experiment.team)
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    for _ in range(5):
        response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 200


@pytest.mark.django_db()
def test_default_settings_change_nothing_but_headers(experiment):
    """Shipped defaults never block; headers are the only addition."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 200
    assert "X-RateLimit-Limit" in response.headers


@pytest.mark.django_db()
def test_non_throttle_errors_keep_their_shape(experiment):
    """The custom exception handler only rewrites Throttled responses."""
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:experiment-detail", args=["00000000-0000-0000-0000-000000000000"]))
    assert response.status_code == 404
    assert "available_in" not in json.loads(response.content)


def test_chat_api_throttle_uses_its_own_scope():
    """Chat API traffic does not draw on the team's interactive api allowance."""
    assert ChatAPIRateThrottle.scope == "chat_api"


def test_chat_api_throttle_keys_on_the_session_when_present():
    """Each conversation gets its own allowance."""
    throttle = ChatAPIRateThrottle()
    request = RequestFactory().post("/")

    identity = throttle.identity(request, _view_stub(session_id="8b1f0c2e-0000-0000-0000-000000000001"))

    assert identity == ("session", "8b1f0c2e-0000-0000-0000-000000000001")


@pytest.mark.django_db()
def test_chat_api_throttle_keys_on_the_channel_before_a_session_exists(experiment):
    """Session creation is bounded per chatbot, since there is no session to key on yet."""
    channel = ExperimentChannelFactory.create(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.EMBEDDED_WIDGET
    )
    throttle = ChatAPIRateThrottle()
    request = RequestFactory().post("/")
    request.auth = channel

    assert throttle.identity(request, _view_stub()) == ("channel", str(channel.pk))


def test_chat_api_throttle_falls_back_to_ip():
    """Legacy clients with neither a session nor a widget channel are still counted."""
    throttle = ChatAPIRateThrottle()
    request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.9")

    assert throttle.identity(request, _view_stub()) == ("ip", "203.0.113.9")


@pytest.mark.django_db()
def test_chat_api_throttle_buckets_an_oauth_caller_on_its_channel(experiment):
    """An OAuth caller reaches `chat/start/` with no session and no embed key, so without the
    channel in `request.auth` it would fall to the IP bucket -- sharing one allowance across every
    machine caller behind a single egress IP, and with unrelated anonymous traffic from that IP.

    `ChatOAuthAuthentication` returning the channel is what closes that, with no change to the
    throttle: bucketing is per channel, exactly as widget traffic already buckets.
    """
    channel = ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        credential_mode=CredentialMode.OAUTH,
        required_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        extra_data={"allowed_domains": []},
    )
    client = ApiTestClient(
        UserFactory.create(),
        experiment.team,
        auth_method="oauth_client_credentials",
        scopes=["chat:start"],
        allowed_chatbots=[experiment],
    )

    with mock.patch("apps.api.throttling.check", wraps=check) as checked:
        response = client.post(
            reverse("api:chat:start-session"),
            data={"chatbot_id": str(experiment.public_id)},
            format="json",
            REMOTE_ADDR="203.0.113.9",
        )

    assert response.status_code == 201, response.json()
    assert [call.args[1:] for call in checked.call_args_list] == [("channel", str(channel.pk))]
