"""Rate limiting behaviour for OAuth token issuance."""

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import resolve, reverse

# Overrides the credentials scope alone, leaving every other scope at its configured rate.
TINY_LIMITS = settings.RATE_LIMITS | {"credentials": {"rate": "2/5m", "fail_open": False}}


def test_token_url_still_reverses_to_the_upstream_name():
    """Shadowing the upstream route must not move the URL or break reverse()."""
    assert reverse("oauth2_provider:token") == "/o/token/"
    assert resolve("/o/token/").view_name == "oauth2_provider:token"


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_token_requests_are_limited_per_ip(client):
    """Repeated token attempts from one address are bounded."""
    url = reverse("oauth2_provider:token")
    client.post(url, {"grant_type": "client_credentials"})
    client.post(url, {"grant_type": "client_credentials"})

    response = client.post(url, {"grant_type": "client_credentials"})

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded."


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_log_only_mode_still_issues_tokens(client):
    """The shipped default records the crossing without refusing issuance."""
    url = reverse("oauth2_provider:token")
    for _ in range(4):
        response = client.post(url, {"grant_type": "client_credentials"})

    assert response.status_code != 429
