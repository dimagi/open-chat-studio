import time
from unittest.mock import patch

import pytest
from oauthlib.oauth2 import BackendApplicationClient

from apps.service_providers.auth_service.oauth import (
    OAuthReconnectRequired,
    OAuthTokenError,
    OAuthTokenManager,
    _config_fingerprint,
    _is_expired,
    _parse_token_response,
    _post_token_request,
    _refresh_authorization_code_token,
)
from apps.service_providers.models import AuthProvider
from apps.service_providers.tests.oauth_authorization_code_test_utils import CONFIG, make_provider


@pytest.fixture(autouse=True)
def _allow_token_url():
    with patch("apps.service_providers.auth_service.oauth.validate_user_input_url"):
        yield


def test_missing_expiry_is_treated_as_expired():
    assert _is_expired({"access_token": "legacy-token"})


def test_authorization_code_token_without_expiry_is_non_expiring():
    client = BackendApplicationClient(client_id="client")
    token = _parse_token_response(client, '{"access_token":"github-token"}', CONFIG["token_url"])
    assert token["expires_at"] is None


@pytest.mark.django_db()
def test_refresh_without_rotation_preserves_refresh_token(team, httpx_mock):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "refresh-old",
        "expires_at": time.time() - 1,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    httpx_mock.add_response(
        url=CONFIG["token_url"],
        json={"access_token": "new", "token_type": "Bearer", "expires_in": 3600},
    )
    assert OAuthTokenManager(provider).get_valid_access_token() == "new"
    request = httpx_mock.get_request()
    assert "refresh_token=refresh-old" in request.content.decode()
    assert AuthProvider.objects.get(pk=provider.pk)._auth_data["refresh_token"] == "refresh-old"


def test_refresh_without_a_refresh_token_requires_reconnect():
    with pytest.raises(OAuthReconnectRequired, match="must be connected again"):
        _refresh_authorization_code_token(CONFIG, {})


@pytest.mark.django_db()
def test_config_change_detected_after_refresh_lock_requires_reconnect(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "old-refresh",
        "expires_at": time.time() - 1,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    with (
        patch(
            "apps.service_providers.auth_service.oauth._authorization_code_config_changed",
            side_effect=[False, True],
        ),
        pytest.raises(OAuthReconnectRequired, match="configuration changed"),
    ):
        OAuthTokenManager(provider).get_valid_access_token()
    provider.refresh_from_db()
    assert provider._auth_data["reconnect_required"] is True


@pytest.mark.parametrize(
    ("payload", "error_code", "error_description"),
    [
        ({"error": "invalid_grant", "error_description": "Token was revoked."}, "invalid_grant", "Token was revoked."),
        ({"error": "invalid_scope"}, "invalid_scope", None),
        ({"error_description": "The requested scope is invalid."}, None, "The requested scope is invalid."),
    ],
)
def test_token_endpoint_structured_error_is_retained(httpx_mock, payload, error_code, error_description):
    httpx_mock.add_response(url=CONFIG["token_url"], status_code=400, json=payload)
    with pytest.raises(OAuthTokenError) as exc_info:
        _post_token_request(CONFIG["token_url"], "grant_type=refresh_token", {})
    assert exc_info.value.error_code == error_code
    assert exc_info.value.error_description == error_description
    assert "grant_type" not in str(exc_info.value)
    assert "refresh_token" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("upstream failure", "text/plain"),
        ('["invalid_scope"]', "application/json"),
    ],
)
def test_http_error_without_oauth_object_uses_generic_message(httpx_mock, body, content_type):
    httpx_mock.add_response(
        url=CONFIG["token_url"],
        status_code=400,
        content=body.encode(),
        headers={"Content-Type": content_type},
    )
    with pytest.raises(OAuthTokenError) as exc_info:
        _post_token_request(CONFIG["token_url"], "grant_type=refresh_token", {})
    assert "Failed to fetch OAuth token" in str(exc_info.value)
    assert exc_info.value.error_code is None
    assert exc_info.value.error_description is None
    assert "client_secret" not in str(exc_info.value)
