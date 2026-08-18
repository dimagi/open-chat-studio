"""Rate limiting behaviour for the OAuth client-credential endpoints: token issuance,
revocation and introspection.
"""

import pytest
from django.conf import settings
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import override_settings
from django.urls import resolve, reverse
from oauth2_provider import views as oauth2_views

# Overrides the credentials scope alone, leaving every other scope at its configured rate.
TINY_LIMITS = settings.RATE_LIMITS | {"credentials": {"rate": "2/5m", "fail_open": False}}

# Every route that authenticates a client_id/client_secret pair, and so is shadowed onto
# the credentials scope in apps/oauth/urls.py.
CREDENTIAL_ROUTES = [
    pytest.param("oauth2_provider:token", "/o/token/", oauth2_views.TokenView, id="token"),
    pytest.param("oauth2_provider:revoke-token", "/o/revoke_token/", oauth2_views.RevokeTokenView, id="revoke-token"),
    pytest.param("oauth2_provider:introspect", "/o/introspect/", oauth2_views.IntrospectTokenView, id="introspect"),
]


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()


@pytest.mark.parametrize(("url_name", "path", "view_class"), CREDENTIAL_ROUTES)
def test_shadowed_routes_still_reverse_to_the_upstream_names(url_name, path, view_class):
    """Shadowing the upstream routes must not move the URLs, break reverse(), or drop the shadow itself.

    The upstream `include()` registers each of these under the same view_name, so asserting on
    `view_name` alone would still pass with the shadowing path() removed. Asserting on the
    resolved view_class instead requires the decorator to actually be in place.
    """
    assert reverse(url_name) == path
    match = resolve(path)
    assert match.view_name == url_name
    assert getattr(match.func.__wrapped__, "view_class", None) is view_class


@pytest.mark.django_db()
@pytest.mark.parametrize(("url_name", "path", "view_class"), CREDENTIAL_ROUTES)
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_client_credential_endpoints_are_rate_limited(client, url_name, path, view_class):
    """Token issuance, revocation and introspection all draw on the same credentials scope."""
    url = reverse(url_name)

    first = client.post(url)
    second = client.post(url)
    third = client.post(url)

    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code == 429
    assert third.json()["detail"] == "Rate limit exceeded."


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
