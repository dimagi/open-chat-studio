from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest
from django.contrib.auth.models import PermissionsMixin
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

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
        patch("apps.service_providers.oauth_views.validate_token_url"),
        patch(
            "apps.service_providers.oauth_views.post_token_request",
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


@pytest.mark.django_db()
def test_callback_escapes_html_in_provider_error(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    response = client.get(
        reverse("service_providers_oauth_callback"),
        {
            "state": state,
            "error": "invalid_scope",
            "error_description": "<img src=x onerror=alert(1)> <script>alert(1)</script>",
        },
    )
    message = " ".join(str(item) for item in get_messages(response.wsgi_request))
    assert "invalid_scope" in message
    assert "&lt;img" in message
    assert "&lt;script&gt;" in message
    assert "<img" not in message
    assert "<script>" not in message


@pytest.mark.django_db()
def test_connect_prunes_stale_pending_oauth_entries_and_keeps_recent_ones(client, team_with_users):
    provider = make_provider(team_with_users)
    client.force_login(team_with_users.members.first())
    session = client.session
    now = timezone.now()
    session["oauth_pending"] = {
        "stale": {
            "created_at": (now - timedelta(seconds=601)).isoformat(),
            "verifier": "old-verifier",
            "provider_id": provider.pk,
            "team_id": provider.team_id,
            "user_id": team_with_users.members.first().pk,
        },
        "recent": {
            "created_at": (now - timedelta(seconds=10)).isoformat(),
            "verifier": "recent-verifier",
            "provider_id": provider.pk,
            "team_id": provider.team_id,
            "user_id": team_with_users.members.first().pk,
        },
    }
    session.save()
    with patch("apps.service_providers.oauth_views.validate_token_url"):
        response = client.get(
            reverse(
                "service_providers:oauth_connect",
                kwargs={"team_slug": provider.team.slug, "pk": provider.pk},
            )
        )
    assert response.status_code == 302
    pending = client.session["oauth_pending"]
    assert "stale" not in pending
    assert "recent" in pending
    newly_created = set(pending) - {"recent"}
    assert len(newly_created) == 1
    entry = pending[newly_created.pop()]
    assert entry["created_at"]
    assert entry["verifier"]


@pytest.mark.django_db()
def test_oauth_pending_state_is_consumed_and_replay_rejected(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    response = client.get(
        reverse("service_providers_oauth_callback"),
        {"state": state, "error": "access_denied"},
    )
    assert response.status_code == 302
    replay = client.get(
        reverse("service_providers_oauth_callback"),
        {"state": state, "error": "access_denied"},
    )
    assert replay.status_code == 400


@pytest.mark.django_db()
def test_callback_escapes_html_in_token_endpoint_error(client, team_with_users):
    provider = make_provider(team_with_users)
    state = connect(client, team_with_users, provider)
    endpoint_error = OAuthTokenError(
        "OAuth token request failed",
        error_code="invalid_scope",
        error_description="<img src=x onerror=alert(1)>",
    )
    with patch("apps.service_providers.oauth_views._exchange_code", side_effect=endpoint_error):
        response = client.get(
            reverse("service_providers_oauth_callback"),
            {"state": state, "code": "authorization-code"},
        )
    message = " ".join(str(item) for item in get_messages(response.wsgi_request))
    assert response.status_code == 302
    assert "invalid_scope" in message
    assert "&lt;img" in message
    assert "<img" not in message
