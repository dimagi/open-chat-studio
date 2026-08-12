"""Rate limiting behaviour for the inbound channel webhooks."""

import pytest
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import RequestFactory

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
            ("channel", "8b1f0c2e-0000-0000-0000-000000000001"),
            id="telegram-keys-on-channel",
        ),
        pytest.param(
            experiment_id_key,
            {"experiment_id": "8b1f0c2e-0000-0000-0000-000000000002"},
            ("channel", "8b1f0c2e-0000-0000-0000-000000000002"),
            id="turn-keys-on-experiment",
        ),
        pytest.param(
            sureadhere_tenant_key,
            {"sureadhere_tenant_id": "42"},
            ("channel", "42"),
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
