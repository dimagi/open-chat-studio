"""Rate limiting behaviour for the public web chat views."""

from unittest import mock

import pytest
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.experiments.rate_limit_keys import public_chat_key
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory

TINY_LIMITS = {"public_chat": {"rate": "2/5m", "fail_open": True}}


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()


@pytest.fixture()
def public_session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment)


@pytest.fixture()
def other_public_session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment)


def _poll_url(session):
    return reverse(
        "experiments:poll_messages",
        args=[session.team.slug, session.experiment.public_id, session.external_id],
    )


def test_key_buckets_per_session_when_the_url_carries_one():
    """Each conversation gets its own allowance."""
    request = RequestFactory().get("/")

    assert public_chat_key(request, session_id="sess-abc") == ("session", "sess-abc")


def test_key_falls_back_to_ip_on_the_session_creating_paths():
    """Public entry points have no session yet, so the caller's address is the bucket."""
    request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.11")

    assert public_chat_key(request) == ("ip", "203.0.113.11")


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_public_entry_point_is_limited_per_ip(client, experiment):
    """Session minting is bounded, so an unauthenticated caller cannot spin up conversations freely."""
    url = reverse(
        "experiments:start_session_public",
        args=[experiment.team.slug, experiment.public_id],
    )
    client.get(url)
    client.get(url)

    response = client.get(url)

    assert response.status_code == 429


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_one_busy_conversation_does_not_starve_another(client, public_session, other_public_session):
    """Per-session bucketing isolates conversations on the same chatbot."""
    busy = _poll_url(public_session)
    quiet = _poll_url(other_public_session)
    client.get(busy)
    client.get(busy)

    over_limit = client.get(busy)
    other_session = client.get(quiet)

    assert over_limit.status_code == 429
    assert other_session.status_code != 429


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_log_only_mode_keeps_the_public_chat_open(client, public_session):
    """The shipped default never interrupts a visitor's conversation."""
    url = _poll_url(public_session)

    for _ in range(4):
        response = client.get(url)

    assert response.status_code != 429


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_a_visitor_over_the_limit_sees_the_error_page(client, public_session):
    """Browser-facing views answer with the site's page, not an API payload."""
    url = _poll_url(public_session)
    client.get(url)
    client.get(url)

    response = client.get(url)

    assert response.status_code == 429
    assert response.headers["Content-Type"].startswith("text/html")
    assert b"Too many requests" in response.content


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
@mock.patch("apps.experiments.services.enqueue_static_triggers", mock.Mock())
@mock.patch("apps.experiments.views.experiment.get_response_for_webchat_task.delay")
def test_sending_messages_is_limited(delay_mock, client, experiment):
    """The path that reaches the chatbot is bounded, since each message costs an LLM call."""
    delay_mock.return_value = mock.Mock(task_id=1)
    session = ExperimentSessionFactory.create(
        experiment=experiment, participant=ParticipantFactory.create(user=experiment.owner)
    )
    url = reverse(
        "experiments:experiment_session_message",
        kwargs={
            "team_slug": experiment.team.slug,
            "experiment_id": experiment.public_id,
            "session_id": session.external_id,
            "version_number": experiment.version_number,
        },
    )
    client.force_login(experiment.owner)
    client.post(url, data={"message": "Hi"})
    client.post(url, data={"message": "Hi"})

    response = client.post(url, data={"message": "Hi"})

    assert response.status_code == 429
    assert delay_mock.call_count == 2


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_waiting_for_a_reply_is_not_limited(client, experiment):
    """The generation poll runs every second while a reply is composed, so it stays outside the scope.

    Counting it would spend a conversation's whole allowance on a single slow answer.
    """
    session = ExperimentSessionFactory.create(experiment=experiment)
    url = reverse(
        "experiments:get_message_response",
        args=[experiment.team.slug, experiment.public_id, session.external_id, "task-1"],
    )
    client.force_login(experiment.owner)

    for _ in range(6):
        response = client.get(url)

    assert response.status_code != 429
