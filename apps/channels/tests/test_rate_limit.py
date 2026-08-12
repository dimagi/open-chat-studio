"""Rate limiting behaviour for the inbound channel webhooks."""

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.channels.rate_limit_keys import (
    channel_external_id_key,
    connect_ip_key,
    experiment_id_key,
    meta_ip_key,
    slack_ip_key,
    sureadhere_tenant_key,
    twilio_ip_key,
)

# Overrides the webhook scope alone, leaving every other scope at its configured rate.
TINY_LIMITS = settings.RATE_LIMITS | {"webhook": {"rate": "2/5m", "fail_open": True}}

META_WEBHOOK_URL_NAME = "channels:new_meta_cloud_api_message"


@pytest.mark.parametrize(
    ("key_fn", "kwargs", "expected"),
    [
        pytest.param(
            channel_external_id_key,
            {"channel_external_id": "8b1f0c2e-0000-0000-0000-000000000001"},
            ("telegram_channel", "8b1f0c2e-0000-0000-0000-000000000001"),
            id="telegram-keys-on-channel",
        ),
        pytest.param(
            experiment_id_key,
            {"experiment_id": "8b1f0c2e-0000-0000-0000-000000000002"},
            ("turn_experiment", "8b1f0c2e-0000-0000-0000-000000000002"),
            id="turn-keys-on-experiment",
        ),
        pytest.param(
            sureadhere_tenant_key,
            {"sureadhere_tenant_id": "42"},
            ("sureadhere_tenant", "42"),
            id="sureadhere-keys-on-tenant",
        ),
    ],
)
def test_key_functions_bucket_on_the_url_identifier(key_fn, kwargs, expected):
    """Each webhook isolates its own channel rather than sharing a provider IP bucket."""
    request = RequestFactory().post("/")

    assert key_fn(request, **kwargs) == expected


@pytest.mark.parametrize(
    ("key_fn", "kwargs"),
    [
        pytest.param(channel_external_id_key, {}, id="telegram"),
        pytest.param(experiment_id_key, {}, id="turn"),
        pytest.param(sureadhere_tenant_key, {}, id="sureadhere"),
    ],
)
def test_key_functions_fall_back_to_ip_without_an_identifier(key_fn, kwargs):
    """A malformed route match still gets counted, under the caller's IP."""
    request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.7")

    assert key_fn(request, **kwargs) == ("ip", "203.0.113.7")


@pytest.mark.parametrize(
    ("key_fn", "expected_identity_type"),
    [
        pytest.param(twilio_ip_key, "twilio_ip", id="twilio"),
        pytest.param(meta_ip_key, "meta_ip", id="meta"),
        pytest.param(connect_ip_key, "connect_ip", id="connect"),
        pytest.param(slack_ip_key, "slack_ip", id="slack"),
    ],
)
def test_address_keyed_webhooks_carry_their_own_identity_type(key_fn, expected_identity_type):
    """Identity type is the only namespace separator in the cache key, so a webhook
    with no URL identifier still needs one of its own.
    """
    request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.7")

    assert key_fn(request) == (expected_identity_type, "203.0.113.7")


def test_address_keyed_webhooks_do_not_share_a_bucket():
    """One address arriving at all four webhooks produces four separate counters."""
    request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.7")

    keys = {key_fn(request) for key_fn in (twilio_ip_key, meta_ip_key, connect_ip_key, slack_ip_key)}

    assert len(keys) == 4


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_telegram_webhook_buckets_per_channel(client):
    """Exhausting one channel's allowance leaves another channel unaffected."""
    noisy = reverse("channels:new_telegram_message", args=["8b1f0c2e-0000-0000-0000-000000000001"])
    quiet = reverse("channels:new_telegram_message", args=["8b1f0c2e-0000-0000-0000-000000000002"])
    client.post(noisy, data="{}", content_type="application/json")
    client.post(noisy, data="{}", content_type="application/json")

    over_limit = client.post(noisy, data="{}", content_type="application/json")
    other_channel = client.post(quiet, data="{}", content_type="application/json")

    assert over_limit.status_code == 429
    assert other_channel.status_code != 429


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_log_only_mode_serves_over_limit_webhook_deliveries(client):
    """The shipped default never drops a provider delivery, even once the identity
    has exhausted its allowance: counting still happens, it is simply not enforced.
    """
    url = reverse("channels:new_telegram_message", args=["8b1f0c2e-0000-0000-0000-000000000003"])
    for _ in range(4):
        response = client.post(url, data="{}", content_type="application/json")

    assert response.status_code != 429
    result = response.wsgi_request.rate_limit_result
    assert result.allowed is True
    assert result.remaining == 0


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
@pytest.mark.parametrize(
    ("url_name", "url_args"),
    [
        pytest.param("channels:new_twilio_message", [], id="twilio"),
        pytest.param("channels:new_sureadhere_message", ["42"], id="sureadhere"),
        pytest.param("channels:new_turn_message", ["8b1f0c2e-0000-0000-0000-000000000004"], id="turn"),
    ],
)
def test_post_only_webhooks_answer_other_methods_without_counting(client, url_name, url_args):
    """The method check runs first, so a GET to a public webhook URL spends no allowance."""
    response = client.get(reverse(url_name, args=url_args))

    assert response.status_code == 405
    assert not hasattr(response.wsgi_request, "rate_limit_result")


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_meta_webhook_answers_an_over_limit_delivery_with_an_empty_200(client):
    """Meta reads a run of non-2xx responses as a broken endpoint and disables the
    subscription for the whole business account, so a limited delivery is dropped the
    same way the route drops every other delivery it declines to process.
    """
    url = reverse(META_WEBHOOK_URL_NAME)
    for _ in range(3):
        response = client.post(url, data="{}", content_type="application/json")

    assert response.wsgi_request.rate_limit_result.allowed is False
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_meta_webhook_verification_handshake_runs_with_an_exhausted_bucket(client, meta_cloud_api_provider):
    """The handshake is how a disabled subscription is restored, so it is not counted."""
    url = reverse(META_WEBHOOK_URL_NAME)
    for _ in range(3):
        client.post(url, data="{}", content_type="application/json")

    response = client.get(
        url,
        {"hub.mode": "subscribe", "hub.verify_token": "test_verify_token", "hub.challenge": "1337"},
    )

    assert response.status_code == 200
    assert response.content == b"1337"
    assert not hasattr(response.wsgi_request, "rate_limit_result")
