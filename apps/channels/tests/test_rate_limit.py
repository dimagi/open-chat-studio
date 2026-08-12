"""Rate limiting behaviour for the inbound channel webhooks."""

import pytest
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.channels.rate_limit_keys import (
    channel_external_id_key,
    experiment_id_key,
    sureadhere_tenant_key,
)

TINY_LIMITS = {"webhook": {"rate": "2/5m", "fail_open": True}}


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()


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
