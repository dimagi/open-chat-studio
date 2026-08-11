"""Tests for the api-scope DRF throttle (issues #2349 / #2140)."""

import json
from datetime import timedelta

import pytest
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.throttling import SimpleRateThrottle
from waffle import get_waffle_flag_model

from apps.api.models import UserAPIKey
from apps.api.throttling import APIRateThrottle, WidgetRateThrottle
from apps.channels.models import ChannelPlatform
from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.rate_limit import RATE_LIMIT_EXEMPT_FLAG
from apps.utils.tests.clients import ApiTestClient

TINY_LIMITS = {"api": {"rate": "3/5m", "fail_open": True}}


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


def test_widget_throttle_uses_its_own_scope():
    """Widget traffic does not draw on the team's interactive api allowance."""
    assert WidgetRateThrottle.scope == "widget"


def test_widget_throttle_keys_on_the_session_when_present():
    """Each conversation gets its own allowance."""
    throttle = WidgetRateThrottle()
    request = RequestFactory().post("/")

    identity = throttle.identity(request, _view_stub(session_id="8b1f0c2e-0000-0000-0000-000000000001"))

    assert identity == ("session", "8b1f0c2e-0000-0000-0000-000000000001")


@pytest.mark.django_db()
def test_widget_throttle_keys_on_the_channel_before_a_session_exists(experiment):
    """Session creation is bounded per chatbot, since there is no session to key on yet."""
    channel = ExperimentChannelFactory.create(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.EMBEDDED_WIDGET
    )
    throttle = WidgetRateThrottle()
    request = RequestFactory().post("/")
    request.auth = channel

    assert throttle.identity(request, _view_stub()) == ("channel", str(channel.pk))


def test_widget_throttle_falls_back_to_ip():
    """Legacy clients with neither a session nor a widget channel are still counted."""
    throttle = WidgetRateThrottle()
    request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.9")

    assert throttle.identity(request, _view_stub()) == ("ip", "203.0.113.9")
