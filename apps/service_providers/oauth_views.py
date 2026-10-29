import re
import secrets

import httpx
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from oauthlib.oauth2 import WebApplicationClient

from apps.service_providers.auth_service.oauth import (
    OAuthTokenError,
    TokenEndpointAuthMethod,
    _authorization_code_config_changed,
    _config_fingerprint,
    _parse_token_response,
    _post_token_request,
    _validate_token_url,
    build_pkce_pair,
)
from apps.service_providers.models import AuthProvider, AuthProviderType
from apps.teams.decorators import login_and_team_required
from apps.teams.utils import current_team
from apps.web.meta import absolute_url

_OAUTH_STATE_SALT = "service-providers-oauth-state"
_OAUTH_STATE_MAX_AGE = 600
_RESERVED_AUTHORIZATION_PARAMS = {
    "client_id",
    "redirect_uri",
    "response_type",
    "state",
    "code_challenge",
    "code_challenge_method",
}


def _oauth_redirect_uri(request):
    return absolute_url(reverse("service_providers_oauth_callback"))


def _pending_state_matches(item, payload, user_id):
    return (
        bool(item)
        and item["user_id"] == user_id
        and item["provider_id"] == payload["provider_id"]
        and item["team_id"] == payload["team_id"]
    )


def _consume_pending_state(request, state):
    payload = signing.loads(state, salt=_OAUTH_STATE_SALT, max_age=_OAUTH_STATE_MAX_AGE)
    pending = request.session.get("oauth_pending", {})
    item = pending.pop(payload["nonce"], None)
    request.session["oauth_pending"] = pending
    request.session.modified = True
    if not _pending_state_matches(item, payload, request.user.pk):
        raise signing.BadSignature("invalid oauth state")
    return payload, item


def _get_provider(payload):
    return get_object_or_404(
        AuthProvider,
        team_id=payload["team_id"],
        pk=payload["provider_id"],
        type=AuthProviderType.oauth_authorization_code.value,
    )


def _provider_edit_url(provider):
    return reverse(
        "service_providers:edit", kwargs={"team_slug": provider.team.slug, "provider_type": "auth", "pk": provider.pk}
    )


def _exchange_code(request, provider, code, verifier):
    _validate_token_url(provider.config["token_url"])
    client = WebApplicationClient(provider.config["client_id"])
    url, _headers, body = client.prepare_token_request(
        provider.config["token_url"],
        redirect_url=_oauth_redirect_uri(request),
        code=code,
        code_verifier=verifier,
        client_secret=provider.config["client_secret"]
        if provider.config.get("token_endpoint_auth_method") == TokenEndpointAuthMethod.CLIENT_SECRET_POST
        else None,
    )
    method = provider.config.get("token_endpoint_auth_method", TokenEndpointAuthMethod.CLIENT_SECRET_BASIC)
    kwargs = (
        {"auth": httpx.BasicAuth(provider.config["client_id"], provider.config["client_secret"])}
        if method == TokenEndpointAuthMethod.CLIENT_SECRET_BASIC
        else {}
    )
    if method == TokenEndpointAuthMethod.CLIENT_SECRET_POST:
        body += "&client_secret=" + provider.config["client_secret"]
    return _parse_token_response(client, _post_token_request(url, body, kwargs), url)


def _persist_token(provider, token):
    provider._auth_data = {**token, "config_fingerprint": _config_fingerprint(provider.config)}
    provider.extra_data = {
        **(provider.extra_data or {}),
        "verified_credentials": True,
        "verification_error": "",
        "credentials_checked_at": timezone.now().isoformat(),
    }
    provider.save(update_fields=["_auth_data", "extra_data"])


def _safe_oauth_error(error_code, error_description, provider, request):
    code = error_code if isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error_code) else ""
    description = error_description if isinstance(error_description, str) else ""
    description = "".join(char for char in description if char.isprintable())[:500]
    sensitive_values = {
        provider.config.get("client_secret", ""),
        provider._auth_data.get("access_token", ""),
        provider._auth_data.get("refresh_token", ""),
        request.GET.get("code", ""),
    }
    for value in sorted((value for value in sensitive_values if value), key=len, reverse=True):
        description = description.replace(value, "[redacted]")
        code = code.replace(value, "[redacted]")
    description = re.sub(
        r"(?i)\b(?:client_secret|access_token|refresh_token|authorization_code|authorization|x-api-key)\b"
        r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
        "[redacted credential]",
        description,
    )
    description = re.sub(
        r"(?i)(?:request[_ -]?headers|headers)\s*[:=]\s*(?:\{[^}]*\}|\[[^\]]*\]|[^\s]+)",
        "[redacted headers]",
        description,
    )
    description = re.sub(
        r"(?i)(?:['\"]?(?:authorization|proxy-authorization|x-api-key|cookie|set-cookie)['\"]?\s*:\s*)['\"]?[^,\r\n]+",
        "[redacted header]",
        description,
    )
    description = re.sub(
        r"(?i)([?&](?:client_secret|access_token|refresh_token|authorization_code|code|authorization)=)[^&#\s]+",
        r"\1[redacted]",
        description,
    )
    parts = [part for part in (code, description) if part]
    return (
        _("OAuth authorization failed: %(details)s") % {"details": "; ".join(parts)}
        if parts
        else _("OAuth authorization failed. Connect the provider again to retry.")
    )


def is_reconnect_required(provider):
    return bool(provider._auth_data.get("reconnect_required") or _authorization_code_config_changed(provider))


@login_and_team_required
@permission_required("service_providers.change_authprovider", raise_exception=True)
def oauth_connect(request, team_slug: str, pk: int):
    provider = get_object_or_404(
        AuthProvider, team=request.team, pk=pk, type=AuthProviderType.oauth_authorization_code.value
    )
    verifier, challenge = build_pkce_pair()
    nonce = secrets.token_urlsafe(24)
    payload = {"provider_id": provider.pk, "team_id": provider.team_id, "user_id": request.user.pk, "nonce": nonce}
    state = signing.dumps(payload, salt=_OAUTH_STATE_SALT)
    pending = request.session.get("oauth_pending", {})
    pending[nonce] = {
        "verifier": verifier,
        "provider_id": provider.pk,
        "team_id": provider.team_id,
        "user_id": request.user.pk,
    }
    request.session["oauth_pending"] = pending
    request.session.modified = True
    _validate_token_url(provider.config["authorize_url"])
    client = WebApplicationClient(provider.config["client_id"])
    extra_params = {
        key: value
        for key, value in (provider.config.get("authorization_params") or {}).items()
        if key not in _RESERVED_AUTHORIZATION_PARAMS
    }
    url = client.prepare_request_uri(
        provider.config["authorize_url"],
        redirect_uri=_oauth_redirect_uri(request),
        scope=(provider.config.get("scope") or "").split() or None,
        state=state,
        code_challenge=challenge,
        code_challenge_method="S256",
        **extra_params,
    )
    return redirect(url)


@login_required
def oauth_callback(request):
    try:
        payload, pending = _consume_pending_state(request, request.GET.get("state"))
    except (signing.BadSignature, KeyError, TypeError):
        return HttpResponseBadRequest("Invalid or expired OAuth state")
    provider = _get_provider(payload)
    with current_team(provider.team):
        if not request.user.has_perm("service_providers.change_authprovider"):
            raise PermissionDenied
        target = _provider_edit_url(provider)
        if request.GET.get("error"):
            messages.error(
                request,
                _safe_oauth_error(
                    request.GET.get("error"),
                    request.GET.get("error_description"),
                    provider,
                    request,
                ),
            )
            return redirect(target)
        code = request.GET.get("code")
        if not code:
            return HttpResponseBadRequest("OAuth callback did not contain an authorization code")
        try:
            token = _exchange_code(request, provider, code, pending["verifier"])
        except OAuthTokenError as exc:
            messages.error(
                request,
                _safe_oauth_error(exc.error_code, exc.error_description, provider, request),
            )
            return redirect(target)
        _persist_token(provider, token)
        messages.success(request, _("OAuth provider connected."))
        return redirect(target)
