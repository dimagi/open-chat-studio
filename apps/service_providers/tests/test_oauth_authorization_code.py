import base64
import hashlib
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import NoReverseMatch, reverse

from apps.service_providers.auth_service.main import BearerTokenAuthService
from apps.service_providers.auth_service.oauth import (
    OAuthReconnectRequired,
    OAuthTokenManager,
    _config_fingerprint,
    build_pkce_pair,
)
from apps.service_providers.models import AuthProvider, AuthProviderType
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.service_provider_factories import AuthProviderFactory

CONFIG = {
    "client_id": "client",
    "client_secret": "secret",
    "authorize_url": "https://auth.example.test/authorize",
    "token_url": "https://auth.example.test/token",
    "scope": "read write",
    "token_endpoint_auth_method": "client_secret_basic",
}


def make_provider(team, **overrides):
    return AuthProviderFactory.create(
        team=team, type=AuthProviderType.oauth_authorization_code, config={**CONFIG, **overrides}
    )


def test_authorization_code_form_and_secret_obfuscation(team_with_users):
    form_cls = AuthProviderType.oauth_authorization_code.form_cls
    form = form_cls(team_with_users, data=CONFIG)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["scope"] == "read write"
    assert set(form.fields["token_endpoint_auth_method"].choices) == {
        ("client_secret_basic", "HTTP Basic"),
        ("client_secret_post", "Request Body (client_secret_post)"),
    }
    invalid = form_cls(team_with_users, data={**CONFIG, "authorize_url": "http://bad.test/auth"})
    assert not invalid.is_valid()
    assert "authorize_url" in invalid.errors


def test_pkce_pair_is_s256():
    verifier, challenge = build_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


@pytest.mark.django_db()
def test_authorization_code_provider_returns_bearer_service(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "access",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    service = provider.get_auth_service()
    assert isinstance(service, BearerTokenAuthService)
    assert service.get_auth_headers() == {"authorization": "Bearer access"}


@pytest.mark.django_db()
def test_refresh_rotates_token(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "refresh-old",
        "expires_at": time.time() - 1,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    refreshed = {
        "access_token": "new",
        "refresh_token": "refresh-new",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
    }
    with patch(
        "apps.service_providers.auth_service.oauth._refresh_authorization_code_token", return_value=refreshed
    ) as refresh:
        assert OAuthTokenManager(provider).get_valid_access_token() == "new"
    refresh.assert_called_once()
    assert AuthProvider.objects.get(pk=provider.pk)._auth_data["refresh_token"] == "refresh-new"


@pytest.mark.django_db()
def test_refresh_without_rotation_preserves_refresh_token(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "refresh-old",
        "expires_at": time.time() - 1,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    refreshed = {"access_token": "new", "token_type": "Bearer", "expires_at": time.time() + 3600}
    with patch(
        "apps.service_providers.auth_service.oauth._refresh_authorization_code_token",
        return_value={**refreshed, "refresh_token": "refresh-old"},
    ):
        assert OAuthTokenManager(provider).get_valid_access_token() == "new"
    assert AuthProvider.objects.get(pk=provider.pk)._auth_data["refresh_token"] == "refresh-old"


@pytest.mark.django_db()
def test_invalid_refresh_requires_reconnect(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "revoked",
        "expires_at": time.time() - 1,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    with patch(
        "apps.service_providers.auth_service.oauth._refresh_authorization_code_token",
        side_effect=OAuthReconnectRequired("reconnect"),
    ):
        with pytest.raises(OAuthReconnectRequired):
            OAuthTokenManager(provider).get_valid_access_token()
    assert AuthProvider.objects.get(pk=provider.pk)._auth_data["refresh_token"] == "revoked"


def test_callback_url_is_global():
    assert reverse("service_providers_oauth_callback") == "/service_providers/auth/oauth/callback/"


def test_callback_url_does_not_accept_team_slug():
    with pytest.raises(NoReverseMatch):
        reverse("service_providers_oauth_callback", kwargs={"team_slug": "other"})


@pytest.mark.django_db()
def test_connect_is_team_scoped_and_generates_pkce_redirect(client, team_with_users):
    provider = make_provider(team_with_users)
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse("service_providers:oauth_connect", kwargs={"team_slug": team_with_users.slug, "pk": provider.pk})
    with patch("apps.service_providers.views._validate_token_url"):
        response = client.get(url)
    assert response.status_code == 302
    query = parse_qs(urlparse(response["Location"]).query)
    assert query["client_id"] == ["client"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["read write"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]
    assert query["redirect_uri"] == ["http://testserver/service_providers/auth/oauth/callback/"]
    assert query["state"]
    assert query["state"][0] in str(client.session.get("oauth_pending")) or client.session.get("oauth_pending")


@pytest.mark.django_db()
def test_connect_rejects_provider_from_another_team(client, team_with_users, team):
    provider = make_provider(team)
    client.force_login(team_with_users.members.first())
    url = reverse("service_providers:oauth_connect", kwargs={"team_slug": team_with_users.slug, "pk": provider.pk})
    response = client.get(url)
    assert response.status_code in (403, 404)


@pytest.mark.django_db()
def test_provider_preset_is_form_only_and_not_saved(team):
    data = {**CONFIG, "provider_preset": "github"}
    form = AuthProviderType.oauth_authorization_code.form_cls(team, data=data)
    assert form.is_valid(), form.errors
    provider = make_provider(team)
    form.save(provider)
    assert "provider_preset" not in provider.config
    assert provider.config["authorize_url"] == CONFIG["authorize_url"]


def test_provider_preset_choices_default_to_custom(team_with_users):
    form = AuthProviderType.oauth_authorization_code.form_cls(team_with_users, data=CONFIG)
    assert form.fields["provider_preset"].initial == "custom"


def _connect(client, team, provider):
    client.force_login(team.members.first())
    url = reverse("service_providers:oauth_connect", kwargs={"team_slug": team.slug, "pk": provider.pk})
    with patch("apps.service_providers.views._validate_token_url"):
        response = client.get(url)
    query = parse_qs(urlparse(response["Location"]).query)
    return query["state"][0]


@pytest.mark.django_db()
def test_callback_exchanges_code_and_persists_tokens(client, team_with_users):
    provider = make_provider(team_with_users)
    original_config = provider.config.copy()
    state = _connect(client, team_with_users, provider)
    with (
        patch("apps.service_providers.views._validate_token_url"),
        patch(
            "apps.service_providers.views._post_token_request",
            return_value='{"access_token":"access","refresh_token":"refresh","token_type":"Bearer","expires_in":3600}',
        ) as exchange,
    ):
        response = client.get(
            reverse("service_providers_oauth_callback"), {"state": state, "code": "authorization-code"}
        )
    assert response.status_code == 302
    exchange.assert_called_once()
    body = exchange.call_args.args[1]
    assert "code_verifier=" in body
    saved = AuthProvider.objects.get(pk=provider.pk)
    assert saved.config == original_config
    assert saved._auth_data["access_token"] == "access"
    assert saved._auth_data["refresh_token"] == "refresh"


@pytest.mark.django_db()
def test_callback_denial_and_replay_are_rejected(client, team_with_users):
    provider = make_provider(team_with_users)
    state = _connect(client, team_with_users, provider)
    denial = client.get(reverse("service_providers_oauth_callback"), {"state": state, "error": "access_denied"})
    assert denial.status_code == 302
    replay = client.get(reverse("service_providers_oauth_callback"), {"state": state, "code": "code"})
    assert replay.status_code == 400


@pytest.mark.django_db()
def test_callback_tampered_state_is_rejected(client, team_with_users):
    provider = make_provider(team_with_users)
    state = _connect(client, team_with_users, provider)
    response = client.get(reverse("service_providers_oauth_callback"), {"state": state + "tampered", "code": "code"})
    assert response.status_code == 400


@pytest.mark.django_db()
def test_authorization_code_refresh_is_single_request_under_row_lock(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "refresh",
        "expires_at": time.time() - 1,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    stale_copy = AuthProvider.objects.get(pk=provider.pk)
    fresh = {
        "access_token": "new",
        "refresh_token": "refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
    }
    with patch(
        "apps.service_providers.auth_service.oauth._refresh_authorization_code_token", return_value=fresh
    ) as refresh:
        assert OAuthTokenManager(provider).get_valid_access_token() == "new"
        assert OAuthTokenManager(stale_copy).get_valid_access_token() == "new"
    refresh.assert_called_once()


@pytest.mark.django_db()
def test_custom_action_uses_authorization_code_bearer_service(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "action-token",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "config_fingerprint": _config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    action = CustomActionFactory.create(team=team, auth_provider=provider)
    assert action.get_auth_service().get_auth_headers() == {"authorization": "Bearer action-token"}
