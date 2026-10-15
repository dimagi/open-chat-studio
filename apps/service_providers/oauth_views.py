import secrets

import httpx
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from oauthlib.oauth2 import WebApplicationClient

from apps.service_providers.auth_service.oauth import (
    OAuthTokenError,
    TokenEndpointAuthMethod,
    _config_fingerprint,
    _parse_token_response,
    _post_token_request,
    _validate_token_url,
    build_pkce_pair,
)
from apps.service_providers.models import AuthProvider, AuthProviderType
from apps.teams.decorators import login_and_team_required
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


def _user_can_change_provider(provider, user):
    membership = provider.team.membership_set.filter(user=user).first()
    return membership is not None and membership.has_perm("service_providers.change_authprovider")


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
    provider.save(update_fields=["_auth_data"])


@login_and_team_required
@permission_required("service_providers.change_authprovider", raise_exception=True)
def oauth_connect(request, team_slug: str, pk: int):
    if request.team.slug != team_slug:
        raise PermissionDenied
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
    if not _user_can_change_provider(provider, request.user):
        raise PermissionDenied
    target = _provider_edit_url(provider)
    if request.GET.get("error"):
        messages.error(request, _("OAuth authorization was denied. Connect the provider again to retry."))
        return redirect(target)
    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("OAuth callback did not contain an authorization code")
    try:
        token = _exchange_code(request, provider, code, pending["verifier"])
    except OAuthTokenError as exc:
        messages.error(request, str(exc))
        return redirect(target)
    _persist_token(provider, token)
    messages.success(request, _("OAuth provider connected."))
    return redirect(target)
