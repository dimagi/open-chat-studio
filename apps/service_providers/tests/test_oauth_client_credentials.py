import time
from unittest.mock import patch

import pytest

from apps.service_providers.auth_service.main import BearerTokenAuthService
from apps.service_providers.auth_service.oauth import (
    DEFAULT_TOKEN_TTL_SECONDS,
    EXPIRY_SKEW_SECONDS,
    OAuthTokenError,
    OAuthTokenManager,
    _config_fingerprint,
)
from apps.service_providers.models import AuthProvider, AuthProviderType
from apps.utils.factories.service_provider_factories import AuthProviderFactory
from apps.utils.urlvalidate import InvalidURL

TOKEN_URL = "https://auth.example.com/token"

CONFIG = {
    "client_id": "my-client",
    "client_secret": "my-secret",
    "token_url": TOKEN_URL,
    "scope": "read write",
    "token_endpoint_auth_method": "client_secret_basic",
}


def _make_provider(team, **config_overrides):
    return AuthProviderFactory.create(
        team=team,
        type=AuthProviderType.oauth_client_credentials,
        config={**CONFIG, **config_overrides},
    )


@pytest.fixture(autouse=True)
def _allow_token_url():
    """Bypass the SSRF guard's DNS resolution for the placeholder token host."""
    with patch("apps.service_providers.auth_service.oauth.validate_user_input_url"):
        yield


# --- Form validation ---


def test_oauth_client_credentials_form_valid(team_with_users):
    form = AuthProviderType.oauth_client_credentials.form_cls(team_with_users, data=CONFIG)
    assert form.is_valid(), form.errors
    assert form.cleaned_data == CONFIG


def test_oauth_client_credentials_form_scope_optional(team_with_users):
    data = {**CONFIG}
    del data["scope"]
    form = AuthProviderType.oauth_client_credentials.form_cls(team_with_users, data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["scope"] == ""


def test_oauth_client_credentials_form_requires_https_token_url(team_with_users):
    form = AuthProviderType.oauth_client_credentials.form_cls(
        team_with_users, data={**CONFIG, "token_url": "http://insecure.example.com/token"}
    )
    assert not form.is_valid()
    assert "token_url" in form.errors


# --- Token fetch & caching ---


@pytest.mark.django_db()
def test_get_auth_service_returns_bearer_token(team):
    provider = _make_provider(team)
    with patch(
        "apps.service_providers.auth_service.oauth._fetch_client_credentials_token",
        return_value={"access_token": "abc123", "token_type": "Bearer", "expires_at": time.time() + 3600},
    ):
        service = provider.get_auth_service()

    assert isinstance(service, BearerTokenAuthService)
    assert service.get_auth_headers() == {"authorization": "Bearer abc123"}


@pytest.mark.django_db()
def test_token_is_cached_and_persisted(team):
    provider = _make_provider(team)
    fetched = {"access_token": "abc123", "token_type": "Bearer", "expires_at": time.time() + 3600}
    with patch(
        "apps.service_providers.auth_service.oauth._fetch_client_credentials_token", return_value=fetched
    ) as mock_fetch:
        assert OAuthTokenManager(provider).get_valid_access_token() == "abc123"
        # Reusing the same in-memory instance hits the fast path (no lock, no fetch).
        assert OAuthTokenManager(provider).get_valid_access_token() == "abc123"

    assert mock_fetch.call_count == 1
    # Token was persisted, so a fresh load from the DB reuses it without fetching.
    reloaded = AuthProvider.objects.get(pk=provider.pk)
    assert reloaded._auth_data["access_token"] == "abc123"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "stored_token",
    [
        pytest.param({}, id="empty"),
        pytest.param({"access_token": "old", "token_type": "Bearer", "expires_at": time.time() - 10}, id="expired"),
        pytest.param(
            {"access_token": "old", "token_type": "Bearer", "expires_at": time.time() + EXPIRY_SKEW_SECONDS - 5},
            id="within-skew",
        ),
    ],
)
def test_refetches_when_token_missing_or_expiring(team, stored_token):
    provider = _make_provider(team)
    provider._auth_data = stored_token
    provider.save(update_fields=["_auth_data"])

    fresh = {"access_token": "fresh", "token_type": "Bearer", "expires_at": time.time() + 3600}
    with patch(
        "apps.service_providers.auth_service.oauth._fetch_client_credentials_token", return_value=fresh
    ) as mock_fetch:
        assert OAuthTokenManager(provider).get_valid_access_token() == "fresh"

    assert mock_fetch.call_count == 1


@pytest.mark.django_db()
def test_valid_token_is_not_refetched(team):
    provider = _make_provider(team)
    provider._auth_data = {
        "access_token": "still-good",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])

    with patch("apps.service_providers.auth_service.oauth._fetch_client_credentials_token") as mock_fetch:
        assert OAuthTokenManager(provider).get_valid_access_token() == "still-good"

    mock_fetch.assert_not_called()


@pytest.mark.django_db()
def test_config_change_invalidates_cached_token(team):
    """Changing a token-relevant config field (e.g. scope) refetches under the new config."""
    provider = _make_provider(team)
    provider._auth_data = {
        "access_token": "old-scope-token",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])

    provider.config = {**provider.config, "scope": "read write admin"}
    provider.save(update_fields=["config"])

    fresh = {"access_token": "new-scope-token", "token_type": "Bearer", "expires_at": time.time() + 3600}
    with patch(
        "apps.service_providers.auth_service.oauth._fetch_client_credentials_token", return_value=fresh
    ) as mock_fetch:
        assert OAuthTokenManager(provider).get_valid_access_token() == "new-scope-token"

    assert mock_fetch.call_count == 1


@pytest.mark.django_db()
def test_concurrent_refresh_results_in_single_request(team):
    """The re-check inside the row lock ensures only one worker fetches a token.

    Two managers hold separate (stale) in-memory copies of the same row -- the
    situation of two Celery workers racing on an expired token. The first fetches
    and persists; the second enters the lock, re-reads the now-valid token via
    ``select_for_update``, and returns it without fetching.
    """
    provider_a = _make_provider(team)
    provider_b = AuthProvider.objects.get(pk=provider_a.pk)
    assert provider_a._auth_data == {}
    assert provider_b._auth_data == {}

    fresh = {"access_token": "shared", "token_type": "Bearer", "expires_at": time.time() + 3600}
    with patch(
        "apps.service_providers.auth_service.oauth._fetch_client_credentials_token", return_value=fresh
    ) as mock_fetch:
        assert OAuthTokenManager(provider_a).get_valid_access_token() == "shared"
        assert OAuthTokenManager(provider_b).get_valid_access_token() == "shared"

    assert mock_fetch.call_count == 1


# --- HTTP token request behaviour ---


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("auth_method", "expect_basic_header"),
    [
        pytest.param("client_secret_basic", True, id="basic"),
        pytest.param("client_secret_post", False, id="post"),
    ],
)
def test_token_request_auth_method(team, httpx_mock, auth_method, expect_basic_header):
    provider = _make_provider(team, token_endpoint_auth_method=auth_method)
    httpx_mock.add_response(
        url=TOKEN_URL,
        json={"access_token": "tok", "token_type": "Bearer", "expires_in": 3600},
    )

    assert OAuthTokenManager(provider).get_valid_access_token() == "tok"

    request = httpx_mock.get_request()
    body = request.content.decode()
    assert "grant_type=client_credentials" in body
    if expect_basic_header:
        # HTTP Basic: credentials in the Authorization header, not the body.
        assert request.headers["Authorization"].startswith("Basic ")
        assert "client_secret=my-secret" not in body
    else:
        assert "Authorization" not in request.headers
        assert "client_secret=my-secret" in body
        assert "client_id=my-client" in body


@pytest.mark.django_db()
def test_token_response_without_expires_in_uses_default_ttl(team):
    provider = _make_provider(team)
    with patch("apps.service_providers.auth_service.oauth.httpx.post") as mock_post:
        mock_post.return_value.text = '{"access_token": "tok", "token_type": "Bearer"}'
        mock_post.return_value.raise_for_status.return_value = None
        before = time.time()
        OAuthTokenManager(provider).get_valid_access_token()

    provider.refresh_from_db()
    expires_at = provider._auth_data["expires_at"]
    assert before + DEFAULT_TOKEN_TTL_SECONDS <= expires_at <= time.time() + DEFAULT_TOKEN_TTL_SECONDS


@pytest.mark.django_db()
def test_token_request_http_error_raises(team, httpx_mock):
    provider = _make_provider(team)
    httpx_mock.add_response(url=TOKEN_URL, status_code=401, json={"error": "invalid_client"})

    with pytest.raises(OAuthTokenError, match="Failed to fetch OAuth token"):
        OAuthTokenManager(provider).get_valid_access_token()


@pytest.mark.django_db()
def test_token_response_missing_access_token_raises(team, httpx_mock):
    provider = _make_provider(team)
    httpx_mock.add_response(url=TOKEN_URL, json={"token_type": "Bearer", "expires_in": 3600})

    with pytest.raises(OAuthTokenError):
        OAuthTokenManager(provider).get_valid_access_token()


@pytest.mark.django_db()
def test_token_url_failing_ssrf_guard_is_rejected(team):
    """A token URL that resolves to an internal host is refused before any request."""
    provider = _make_provider(team)
    with patch(
        "apps.service_providers.auth_service.oauth.validate_user_input_url",
        side_effect=InvalidURL("Unsafe IP address"),
    ):
        with pytest.raises(OAuthTokenError, match="Invalid OAuth token URL"):
            OAuthTokenManager(provider).get_valid_access_token()
