import base64
import hashlib
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth.models import PermissionsMixin
from django.contrib.messages import get_messages
from django.urls import NoReverseMatch, reverse

from apps.service_providers.auth_service.main import BearerTokenAuthService
from apps.service_providers.auth_service.oauth import (
    OAuthReconnectRequired,
    OAuthTokenError,
    OAuthTokenManager,
    _refresh_authorization_code_token,
    build_pkce_pair,
    config_fingerprint,
)
from apps.service_providers.models import AuthProvider, AuthProviderType
from apps.service_providers.tests.oauth_authorization_code_test_utils import CONFIG, connect, make_provider
from apps.teams.utils import get_current_team
from apps.utils.factories.custom_actions import CustomActionFactory


@pytest.fixture(autouse=True)
def _allow_token_url():
    with patch("apps.service_providers.auth_service.oauth.validate_user_input_url"):
        yield


def test_authorization_code_form_and_secret_obfuscation(team_with_users):
    form_cls = AuthProviderType.oauth_authorization_code.form_cls
    form = form_cls(team_with_users, data=CONFIG)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["scope"] == "read write"
    assert set(form.fields["token_endpoint_auth_method"].choices) == {
        ("client_secret_basic", "HTTP Basic"),
        ("client_secret_post", "Request Body (client_secret_post)"),
    }
    assert "Space separated list of scopes" in form.fields["scope"].help_text
    assert form.fields["client_id"].help_text
    assert form.fields["client_secret"].help_text
    assert form.fields["authorize_url"].help_text
    assert form.fields["token_url"].help_text
    assert form.fields["token_endpoint_auth_method"].help_text
    assert form.fields["authorization_params"].help_text
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
        "config_fingerprint": config_fingerprint(provider.config),
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
        "config_fingerprint": config_fingerprint(provider.config),
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
def test_invalid_refresh_requires_reconnect(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "revoked",
        "expires_at": time.time() - 1,
        "config_fingerprint": config_fingerprint(provider.config),
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
    with patch("apps.service_providers.oauth_views.validate_token_url"):
        response = client.get(url)
    assert response.status_code == 302
    query = parse_qs(urlparse(response["Location"]).query)
    assert query["client_id"] == ["client"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["read write"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]
    assert query["redirect_uri"][0].endswith("/service_providers/auth/oauth/callback/")
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


@pytest.mark.django_db()
def test_callback_denial_and_replay_are_rejected(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    denial = client.get(reverse("service_providers_oauth_callback"), {"state": state, "error": "access_denied"})
    assert denial.status_code == 302
    replay = client.get(reverse("service_providers_oauth_callback"), {"state": state, "code": "code"})
    assert replay.status_code == 400


@pytest.mark.django_db()
def test_callback_tampered_state_is_rejected(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    response = client.get(reverse("service_providers_oauth_callback"), {"state": state + "tampered", "code": "code"})
    assert response.status_code == 400


@pytest.mark.django_db()
def test_authorization_code_refresh_is_single_request_under_row_lock(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "refresh",
        "expires_at": time.time() - 1,
        "config_fingerprint": config_fingerprint(provider.config),
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
        "config_fingerprint": config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    action = CustomActionFactory.create(team=team, auth_provider=provider)
    assert action.get_auth_service().get_auth_headers() == {"authorization": "Bearer action-token"}


@pytest.mark.django_db()
def test_non_expiring_authorization_code_token_is_reused(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "github-token",
        "token_type": "Bearer",
        "expires_at": None,
        "config_fingerprint": config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    with patch("apps.service_providers.auth_service.oauth._refresh_authorization_code_token") as refresh:
        assert OAuthTokenManager(provider).get_valid_access_token() == "github-token"
    refresh.assert_not_called()


def test_refresh_invalid_grant_requires_reconnect():
    config = {**CONFIG}
    with patch(
        "apps.service_providers.auth_service.oauth.post_token_request",
        return_value='{"error":"invalid_grant","error_description":"revoked"}',
    ):
        with pytest.raises(OAuthReconnectRequired):
            _refresh_authorization_code_token(config, {"refresh_token": "revoked"})


def test_refresh_transport_error_is_not_reconnect():
    with patch("apps.service_providers.auth_service.oauth.post_token_request", side_effect=OAuthTokenError("timeout")):
        with pytest.raises(OAuthTokenError) as error:
            _refresh_authorization_code_token(CONFIG, {"refresh_token": "refresh"})
    assert not isinstance(error.value, OAuthReconnectRequired)


@pytest.mark.django_db()
def test_config_change_requires_reconnect_before_refresh(team):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old",
        "refresh_token": "old-refresh",
        "expires_at": time.time() - 1,
        "config_fingerprint": config_fingerprint(provider.config),
    }
    provider.save(update_fields=["_auth_data"])
    provider.config = {**provider.config, "token_url": "https://new.example.test/token"}
    provider.save(update_fields=["config"])
    with patch("apps.service_providers.auth_service.oauth._refresh_authorization_code_token") as refresh:
        with pytest.raises(OAuthReconnectRequired):
            OAuthTokenManager(provider).get_valid_access_token()
    refresh.assert_not_called()
    assert AuthProvider.objects.get(pk=provider.pk)._auth_data["reconnect_required"] is True


@pytest.mark.django_db()
def test_google_preset_and_custom_authorization_params_are_saved(team):
    google = AuthProviderType.oauth_authorization_code.form_cls(team, data={**CONFIG, "provider_preset": "google"})
    assert google.is_valid(), google.errors
    provider = make_provider(team)
    google.save(provider)
    assert provider.config["authorization_params"] == {"access_type": "offline", "prompt": "consent"}

    custom = AuthProviderType.oauth_authorization_code.form_cls(
        team, data={**CONFIG, "authorization_params": {"audience": "docs"}}
    )
    assert custom.is_valid(), custom.errors
    custom.save(provider)
    assert provider.config["authorization_params"] == {"audience": "docs"}


@pytest.mark.django_db()
def test_connect_includes_valid_custom_authorization_params(client, team_with_users):
    provider = make_provider(
        team_with_users,
        authorization_params={
            "audience": "docs",
            "access_type": "offline",
        },
    )
    client.force_login(team_with_users.members.first())
    url = reverse("service_providers:oauth_connect", kwargs={"team_slug": team_with_users.slug, "pk": provider.pk})
    with patch("apps.service_providers.oauth_views.validate_token_url"):
        response = client.get(url)
    query = parse_qs(urlparse(response["Location"]).query)
    assert query["audience"] == ["docs"]
    assert query["access_type"] == ["offline"]
    assert query["client_id"] == ["client"]
    assert query["response_type"] == ["code"]


def test_refresh_request_contains_expected_field_names(httpx_mock):
    httpx_mock.add_response(
        url="https://auth.example.test/token",
        status_code=400,
        json={"error": "invalid_request", "error_description": "bad request"},
    )
    with patch("apps.service_providers.auth_service.oauth.validate_token_url"), pytest.raises(OAuthTokenError):
        _refresh_authorization_code_token(
            {**CONFIG, "token_endpoint_auth_method": "client_secret_post"},
            {"refresh_token": "refresh"},
        )
    request = httpx_mock.get_request()
    fields = {part.split("=", 1)[0] for part in request.content.decode().split("&")}
    assert {"grant_type", "refresh_token", "client_id", "client_secret"} <= fields


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_id", "new-client"),
        ("client_secret", "new-secret"),
        ("authorize_url", "https://auth.example.test/new-authorize"),
        ("token_url", "https://auth.example.test/new-token"),
        ("scope", "read admin"),
        ("authorization_params", {"access_type": "online"}),
        ("token_endpoint_auth_method", "client_secret_post"),
    ],
)
def test_authorization_code_config_changes_require_reconnect(team, field, value):
    provider = make_provider(team)
    provider._auth_data = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "config_fingerprint": config_fingerprint(provider.config),
    }
    provider.extra_data = {"verified_credentials": True}
    provider.config = {**provider.config, field: value}
    provider.save()
    assert provider.verification_state == "failed"
    assert not provider.credentials_verified
    with pytest.raises(OAuthReconnectRequired, match="configuration changed"):
        OAuthTokenManager(provider).get_valid_access_token()
    provider.refresh_from_db()
    assert provider._auth_data["reconnect_required"] is True


@pytest.mark.django_db()
def test_create_form_shows_public_callback_uri(client, team_with_users):
    client.force_login(team_with_users.members.first())
    url = reverse(
        "service_providers:new",
        kwargs={
            "team_slug": team_with_users.slug,
            "provider_type": "auth",
            "subtype": "oauth_authorization_code",
        },
    )
    response = client.get(url)
    assert response.status_code == 200
    assert b"Callback URI (register this with your provider)" in response.content
    assert b"/service_providers/auth/oauth/callback/" in response.content
    assert b'id="id_authorization_params"' in response.content


@pytest.mark.django_db()
def test_callback_preserves_safe_oauth_error_and_redacts_credentials(client, team_with_users):
    provider = make_provider(team_with_users)
    provider._auth_data = {
        "access_token": "access-token-secret",
        "refresh_token": "refresh-token-secret",
    }
    provider.save(update_fields=["_auth_data"])
    state = connect(client, team_with_users, provider)
    description = (
        "Requested scope is invalid; client_secret=secret access_token=access-token-secret "
        "refresh_token=refresh-token-secret authorization_code=authorization-code"
        "request_headers={'Authorization': 'Bearer header-secret', 'X-API-Key': 'header-key'}"
    )
    response = client.get(
        reverse("service_providers_oauth_callback"),
        {
            "state": state,
            "error": "invalid_scope",
            "error_description": description,
            "code": "authorization-code",
        },
    )
    assert response.status_code == 302
    message = " ".join(str(item) for item in get_messages(response.wsgi_request))
    assert "invalid_scope" in message
    assert "Requested scope is invalid" in message
    for credential in (
        "secret",
        "access-token-secret",
        "refresh-token-secret",
        "authorization-code",
        "header-secret",
        "header-key",
    ):
        assert credential not in message


@pytest.mark.django_db()
def test_callback_permission_check_runs_in_provider_team_context(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    observed_teams = []

    def has_team_permission(user, permission, obj=None):
        observed_teams.append(get_current_team())
        return permission == "service_providers.change_authprovider" and get_current_team() == provider.team

    with patch.object(PermissionsMixin, "has_perm", autospec=True, side_effect=has_team_permission):
        response = client.get(
            reverse("service_providers_oauth_callback"),
            {"state": state, "error": "access_denied"},
        )
    assert response.status_code == 302
    assert observed_teams == [provider.team]


@pytest.mark.django_db()
def test_edit_form_shows_reconnect_when_oauth_config_changes(client, team_with_users):
    provider = make_provider(team_with_users)
    provider._auth_data = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "config_fingerprint": config_fingerprint(provider.config),
    }
    provider.config = {**provider.config, "scope": "read admin"}
    provider.save()
    client.force_login(team_with_users.members.first())
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "auth", "pk": provider.pk},
    )
    response = client.get(url)
    assert response.status_code == 200
    assert b"Status: reconnect required" in response.content


@pytest.mark.parametrize(
    "params",
    [
        [],
        "foo",
        42,
        True,
        {"scope": "admin"},
        {"state": "attacker"},
        {"client_id": "attacker"},
        {"redirect_uri": "https://attacker.example/callback"},
        {"response_type": "token"},
        {"code_challenge": "attacker"},
        {"code_challenge_method": "plain"},
    ],
)
def test_authorization_params_reject_non_objects_and_reserved_protocol_fields(team_with_users, params):
    form_cls = AuthProviderType.oauth_authorization_code.form_cls
    form = form_cls(team_with_users, data={**CONFIG, "authorization_params": params})
    assert not form.is_valid()
    assert "authorization_params" in form.errors


def test_empty_authorization_params_normalize_to_empty_object(team_with_users):
    form_cls = AuthProviderType.oauth_authorization_code.form_cls
    form = form_cls(team_with_users, data={**CONFIG, "authorization_params": ""})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["authorization_params"] == {}


@pytest.mark.django_db()
def test_connect_rejects_invalid_saved_authorization_params(client, team_with_users):
    provider = make_provider(team_with_users, authorization_params={"scope": "admin"})
    client.force_login(team_with_users.members.first())
    response = client.get(
        reverse(
            "service_providers:oauth_connect",
            kwargs={"team_slug": provider.team.slug, "pk": provider.pk},
        )
    )
    assert response.status_code == 302
    assert "cannot override reserved OAuth fields" in " ".join(
        str(item) for item in get_messages(response.wsgi_request)
    )
