"""Rate limiting behaviour for the embedded widget chat endpoints."""

from types import SimpleNamespace

import pytest
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.throttling import ChatAPIRateThrottle
from apps.api.views.chat import (
    chat_poll_response,
    chat_poll_task_response,
    chat_renew_session_token,
    chat_send_message,
    chat_start_session,
    chat_upload_file,
)
from apps.oauth.models import OAuth2AccessToken
from apps.utils.factories.experiment import ExperimentSessionFactory

TINY_LIMITS = {"chat_api": {"rate": "2/5m", "fail_open": True}}

CHAT_VIEWS = [
    pytest.param(chat_start_session, id="start-session"),
    pytest.param(chat_send_message, id="send-message"),
    pytest.param(chat_poll_response, id="poll-response"),
    pytest.param(chat_poll_task_response, id="poll-task-response"),
    pytest.param(chat_upload_file, id="upload-file"),
    pytest.param(chat_renew_session_token, id="renew-session-token"),
]


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def widget_session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)


@pytest.fixture()
def other_widget_session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)


@pytest.mark.parametrize("view", CHAT_VIEWS)
def test_chat_endpoints_use_the_chat_api_throttle(view):
    """No chat endpoint is left drawing on the shared api allowance."""
    assert view.cls.throttle_classes == [ChatAPIRateThrottle]


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_one_busy_session_does_not_starve_another(api_client, widget_session, other_widget_session):
    """Per-session bucketing isolates conversations on the same chatbot."""
    busy = reverse("api:chat:poll-response", args=[widget_session.external_id])
    quiet = reverse("api:chat:poll-response", args=[other_widget_session.external_id])
    api_client.get(busy)
    api_client.get(busy)

    over_limit = api_client.get(busy)
    other_session = api_client.get(quiet)

    assert over_limit.status_code == 429
    assert other_session.status_code != 429


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_log_only_mode_keeps_serving_the_conversation(api_client, widget_session):
    """The shipped default never interrupts a live chat."""
    url = reverse("api:chat:poll-response", args=[widget_session.external_id])
    for _ in range(4):
        response = api_client.get(url)

    assert response.status_code != 429
    assert response["X-RateLimit-Limit"] == "2"


def test_host_renewals_do_not_share_the_visitor_bucket():
    """The host's OAuth client is the principal on a renewal, not the session in the URL."""
    request = SimpleNamespace(auth=OAuth2AccessToken(application_id=42), team=None)
    view = SimpleNamespace(kwargs={"session_id": "abc"})

    assert ChatAPIRateThrottle().identity(request, view) == ("oauth_client", "42")
