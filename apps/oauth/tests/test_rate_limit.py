"""Rate limiting behaviour for OAuth token issuance."""

import pytest
from django.conf import settings
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import override_settings
from django.urls import resolve, reverse

# Overrides the credentials scope alone, leaving every other scope at its configured rate.
TINY_LIMITS = settings.RATE_LIMITS | {"credentials": {"rate": "2/5m", "fail_open": False}}


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()


def test_token_url_still_reverses_to_the_upstream_name():
    """Shadowing the upstream route must not move the URL or break reverse()."""
    assert reverse("oauth2_provider:token") == "/o/token/"
    assert resolve("/o/token/").view_name == "oauth2_provider:token"


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_token_requests_are_limited_per_ip(client):
    """Repeated token attempts from one address are bounded; another address is unaffected."""
    url = reverse("oauth2_provider:token")

    first = client.post(url, {"grant_type": "client_credentials"})
    second = client.post(url, {"grant_type": "client_credentials"})
    third = client.post(url, {"grant_type": "client_credentials"})

    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code == 429
    assert third.json()["detail"] == "Rate limit exceeded."

    other_ip_response = client.post(url, {"grant_type": "client_credentials"}, REMOTE_ADDR="203.0.113.9")

    assert other_ip_response.status_code != 429


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_log_only_mode_still_issues_tokens(client):
    """The shipped default records the crossing without refusing issuance."""
    url = reverse("oauth2_provider:token")
    for _ in range(4):
        response = client.post(url, {"grant_type": "client_credentials"})

    assert response.status_code != 429
