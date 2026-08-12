"""Rate limiting behaviour for failed API-key authentication."""

import pytest
from django.conf import settings
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import override_settings
from django.urls import reverse

# Overrides the credentials scope alone, leaving every other scope at its configured rate.
TINY_LIMITS = settings.RATE_LIMITS | {"credentials": {"rate": "2/5m", "fail_open": False}}

INVALID_KEY_HEADERS = {"x-api-key": "not-a-real-key"}


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_repeated_invalid_keys_are_throttled(client):
    """Brute forcing the key header is bounded per address."""
    url = reverse("api:experiment-list")
    assert client.get(url, headers=INVALID_KEY_HEADERS).status_code == 401
    assert client.get(url, headers=INVALID_KEY_HEADERS).status_code == 401

    response = client.get(url, headers=INVALID_KEY_HEADERS)

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded."


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_log_only_mode_still_returns_the_authentication_error(client):
    """The shipped default leaves the 401 contract untouched."""
    url = reverse("api:experiment-list")
    for _ in range(4):
        response = client.get(url, headers=INVALID_KEY_HEADERS)

    assert response.status_code == 401
