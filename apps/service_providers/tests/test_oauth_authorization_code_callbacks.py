from unittest.mock import patch
from urllib.parse import parse_qs

import pytest
from django.contrib.auth.models import PermissionsMixin
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.service_providers.auth_service.oauth import OAuthTokenError
from apps.service_providers.models import AuthProvider
from apps.service_providers.tests.oauth_authorization_code_test_utils import connect, make_provider


@pytest.mark.parametrize("auth_method", ["client_secret_basic", "client_secret_post"])
@pytest.mark.django_db()
def test_callback_exchanges_code_and_persists_tokens(client, team_with_users, auth_method):
    provider = make_provider(team_with_users, token_endpoint_auth_method=auth_method)
    original_config = provider.config.copy()
    state = connect(client, team_with_users, provider)
    with (
        patch("apps.service_providers.oauth_views._validate_token_url"),
        patch(
            "apps.service_providers.oauth_views._post_token_request",
            return_value='{"access_token":"access","refresh_token":"refresh","token_type":"Bearer","expires_in":3600}',
        ) as exchange,
    ):
        response = client.get(
            reverse("service_providers_oauth_callback"), {"state": state, "code": "authorization-code"}
        )
    assert response.status_code == 302
    exchange.assert_called_once()
    body = exchange.call_args.args[1]
    request_kwargs = exchange.call_args.args[2]
    assert "code_verifier=" in body
    if auth_method == "client_secret_post":
        assert parse_qs(body)["client_secret"] == ["secret"]
        assert request_kwargs == {}
    else:
        assert "client_secret" not in parse_qs(body)
        assert "auth" in request_kwargs
    saved = AuthProvider.objects.get(pk=provider.pk)
    assert saved.config == original_config
    assert saved._auth_data["access_token"] == "access"
    assert saved._auth_data["refresh_token"] == "refresh"
    assert saved.credentials_verified
    assert saved.verification_state == "verified"
    assert saved.credentials_checked_at is not None


@pytest.mark.django_db()
def test_callback_without_code_is_rejected(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    response = client.get(reverse("service_providers_oauth_callback"), {"state": state})
    assert response.status_code == 400
    assert b"did not contain an authorization code" in response.content


@pytest.mark.django_db()
def test_callback_permission_denial_is_enforced(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    with patch.object(PermissionsMixin, "has_perm", return_value=False):
        response = client.get(reverse("service_providers_oauth_callback"), {"state": state, "error": "access_denied"})
    assert response.status_code == 403


@pytest.mark.django_db()
def test_callback_exchange_failure_is_safe_and_redirects(client, team_with_users):
    provider = make_provider(team_with_users)
    provider._auth_data = {
        "access_token": "access-token-secret",
        "refresh_token": "refresh-token-secret",
    }
    provider.save(update_fields=["_auth_data"])
    state = connect(client, team_with_users, provider)
    error = OAuthTokenError(
        "invalid token response",
        error_code="invalid_client",
        error_description="access_token=access-token-secret authorization_code=authorization-code",
    )
    with patch("apps.service_providers.oauth_views._exchange_code", side_effect=error):
        response = client.get(
            reverse("service_providers_oauth_callback"),
            {"state": state, "code": "authorization-code"},
        )
    assert response.status_code == 302
    message = " ".join(str(item) for item in get_messages(response.wsgi_request))
    assert "invalid_client" in message
    assert "access-token-secret" not in message
    assert "refresh-token-secret" not in message
    assert "authorization-code" not in message
