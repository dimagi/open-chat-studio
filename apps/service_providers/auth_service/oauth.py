import base64
import hashlib
import json
import secrets
import time
from typing import TYPE_CHECKING

import httpx
from django.conf import settings
from django.db import transaction
from oauthlib.oauth2 import BackendApplicationClient, OAuth2Error, WebApplicationClient

from apps.utils.urlvalidate import InvalidURL, validate_user_input_url

if TYPE_CHECKING:
    from apps.service_providers.models import AuthProvider

# Refetch this many seconds before the token's real expiry so a token can't
# lapse between the validity check and the request landing at the remote server.
EXPIRY_SKEW_SECONDS = 60

# Fallback lifetime used when the token endpoint omits `expires_in`. Caching for
# a bounded window is better than refetching on every request.
DEFAULT_TOKEN_TTL_SECONDS = 3600

# Config fields the token depends on. A change to any of them invalidates the
# cached token so the next request refetches under the new config.
_FINGERPRINTED_CONFIG_FIELDS = (
    "client_id",
    "client_secret",
    "token_url",
    "scope",
    "token_endpoint_auth_method",
)


class TokenEndpointAuthMethod:
    CLIENT_SECRET_BASIC = "client_secret_basic"
    CLIENT_SECRET_POST = "client_secret_post"


class OAuthTokenError(Exception):
    """Raised when an OAuth access token cannot be obtained."""

    def __init__(self, message, *, error_code=None, error_description=None):
        super().__init__(message)
        self.error_code = error_code
        self.error_description = error_description


class OAuthReconnectRequired(OAuthTokenError):
    """The provider must be connected again by an administrator."""


class OAuthTokenManager:
    """Resolves a valid OAuth access token for an ``AuthProvider``.

    All the stateful logic (fetch, cache, refetch on expiry) lives here so that
    ``AuthProvider.get_auth_service()`` can collapse OAuth to "produce a valid
    bearer token, then it's just bearer auth".
    """

    def __init__(self, provider: "AuthProvider"):
        self.provider = provider

    def get_valid_access_token(self) -> str:
        token = self.provider._auth_data
        if _token_is_valid(token, self.provider.config):
            return token["access_token"]
        if _authorization_code_config_changed(self.provider):
            self._mark_reconnect_required()
            raise OAuthReconnectRequired("OAuth configuration changed; connect the provider again")
        return self._refetch_with_lock()

    def _mark_reconnect_required(self) -> None:
        model = type(self.provider)
        with transaction.atomic():
            provider = model.objects.select_for_update().get(pk=self.provider.pk)
            self._persist_reconnect_required(provider)
            self.provider._auth_data = provider._auth_data

    @staticmethod
    def _persist_reconnect_required(provider) -> None:
        provider._auth_data = {**provider._auth_data, "reconnect_required": True}
        provider.save(update_fields=["_auth_data"])

    @staticmethod
    def _persist_token(provider, token) -> None:
        token["config_fingerprint"] = _config_fingerprint(provider.config)
        token.pop("reconnect_required", None)
        provider._auth_data = token
        provider.save(update_fields=["_auth_data"])

    @staticmethod
    def _get_fresh_token(provider) -> dict:
        if _authorization_code_config_changed(provider):
            raise OAuthReconnectRequired("OAuth configuration changed; connect the provider again")
        if provider.type == "oauth_authorization_code":
            return _refresh_authorization_code_token(provider.config, provider._auth_data)
        return _fetch_client_credentials_token(provider.config)

    def _refetch_with_lock(self) -> str:
        """Fetch a fresh token under a row lock and re-check after waiting."""
        model = type(self.provider)
        with transaction.atomic():
            provider = model.objects.select_for_update().get(pk=self.provider.pk)
            if _token_is_valid(provider._auth_data, provider.config):
                token = provider._auth_data
            else:
                try:
                    token = self._get_fresh_token(provider)
                except OAuthReconnectRequired:
                    if provider.type == "oauth_authorization_code":
                        self._persist_reconnect_required(provider)
                    raise
                self._persist_token(provider, token)
            self.provider._auth_data = token
        return token["access_token"]


def _authorization_code_config_changed(provider) -> bool:
    if provider.type != "oauth_authorization_code":
        return False
    fingerprint = provider._auth_data.get("config_fingerprint")
    return bool(fingerprint) and fingerprint != _config_fingerprint(provider.config)


def _token_is_valid(token: dict, config: dict) -> bool:
    """A cached token is usable while it is unexpired and was issued under the current config."""
    if not token:
        return False
    if token.get("config_fingerprint") != _config_fingerprint(config):
        return False
    return not _is_expired(token)


def _config_fingerprint(config: dict) -> str:
    relevant = {field: config.get(field) for field in _FINGERPRINTED_CONFIG_FIELDS}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def _is_expired(token: dict) -> bool:
    expires_at = token.get("expires_at")
    if "expires_at" not in token:
        return True
    if expires_at is None:
        return False
    return time.time() >= (expires_at - EXPIRY_SKEW_SECONDS)


def _fetch_client_credentials_token(config: dict) -> dict:
    """Perform the client-credentials token request and return the token dict."""
    token_url = config["token_url"]
    scope = config.get("scope") or None
    _validate_token_url(token_url)

    # The scope is set on the request body only, not on the client. A server may
    # grant a different scope than requested (RFC 6749 §3.3); if the client held a
    # scope, oauthlib would raise on that mismatch when parsing. We don't consume
    # the granted scope, so we let it differ silently.
    client = BackendApplicationClient(client_id=config["client_id"])
    body, request_kwargs = _prepare_token_request(client, config, scope)
    response_text = _post_token_request(token_url, body, request_kwargs)
    return _parse_token_response(client, response_text, token_url, default_ttl=DEFAULT_TOKEN_TTL_SECONDS)


def build_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _refresh_authorization_code_token(config: dict, old_token: dict) -> dict:
    refresh_token = old_token.get("refresh_token")
    if not refresh_token:
        raise OAuthReconnectRequired("OAuth authorization must be connected again")
    _validate_token_url(config["token_url"])
    client = WebApplicationClient(client_id=config["client_id"])
    method = config.get("token_endpoint_auth_method", TokenEndpointAuthMethod.CLIENT_SECRET_BASIC)
    body = client.prepare_refresh_body(
        refresh_token=refresh_token,
        scope=config.get("scope") or None,
        include_client_id=method == TokenEndpointAuthMethod.CLIENT_SECRET_POST,
        client_id=config["client_id"],
        client_secret=config["client_secret"] if method == TokenEndpointAuthMethod.CLIENT_SECRET_POST else None,
    )
    kwargs = (
        {}
        if method == TokenEndpointAuthMethod.CLIENT_SECRET_POST
        else {"auth": httpx.BasicAuth(config["client_id"], config["client_secret"])}
    )
    try:
        token = _parse_token_response(
            client, _post_token_request(config["token_url"], body, kwargs), config["token_url"]
        )
    except OAuthTokenError as exc:
        if exc.error_code == "invalid_grant":
            raise OAuthReconnectRequired(str(exc)) from exc
        raise
    token.setdefault("refresh_token", refresh_token)
    return token


def _validate_token_url(token_url: str) -> None:
    """Guard the outbound request against SSRF.

    The token endpoint is admin-supplied config and carries the client secret, so
    it must not be allowed to target internal hosts.
    """
    try:
        validate_user_input_url(token_url, strict=not settings.DEBUG)
    except InvalidURL as exc:
        raise OAuthTokenError(f"Invalid OAuth token URL {token_url}: {exc}") from exc


def _prepare_token_request(client: BackendApplicationClient, config: dict, scope) -> tuple[str, dict]:
    """Build the request body and httpx kwargs for the configured auth method."""
    auth_method = config.get("token_endpoint_auth_method", TokenEndpointAuthMethod.CLIENT_SECRET_BASIC)
    if auth_method == TokenEndpointAuthMethod.CLIENT_SECRET_POST:
        body = client.prepare_request_body(scope=scope, include_client_id=True, client_secret=config["client_secret"])
        return body, {}
    body = client.prepare_request_body(scope=scope)
    return body, {"auth": httpx.BasicAuth(config["client_id"], config["client_secret"])}


def _post_token_request(token_url: str, body: str, request_kwargs: dict) -> str:
    """POST the token request, returning the response body text."""
    try:
        response = httpx.post(
            token_url,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            timeout=settings.RESTRICTED_HTTP_MAX_TIMEOUT,
            **request_kwargs,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        if response is not None and response.is_error:
            error_code, error_description = _extract_oauth_error(response)
            if error_code or error_description:
                details = f"OAuth token request failed ({response.status_code})"
                if error_code:
                    details += f": {error_code}"
                if error_description:
                    details += f" - {error_description}"
                raise OAuthTokenError(details, error_code=error_code, error_description=error_description) from exc
        raise OAuthTokenError(f"Failed to fetch OAuth token from {token_url}: {exc}") from exc
    return response.text


def _extract_oauth_error(response: httpx.Response) -> tuple[str | None, str | None]:
    try:
        payload = response.json()
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    error_code = payload.get("error")
    error_description = payload.get("error_description")
    return (
        error_code if isinstance(error_code, str) else None,
        error_description if isinstance(error_description, str) else None,
    )


def _parse_token_response(
    client: BackendApplicationClient, response_text: str, token_url: str, default_ttl: int | None = None
) -> dict:
    """Parse the token response and normalise it to the fields we persist."""
    try:
        token = client.parse_request_body_response(response_text)
    except OAuth2Error as exc:
        raise OAuthTokenError(
            f"Invalid OAuth token response from {token_url}: {exc}", error_code=getattr(exc, "error", None)
        ) from exc

    if not token.get("access_token"):
        raise OAuthTokenError(f"OAuth token response from {token_url} did not contain an access token")

    # Persist only the fields we need; drop oauthlib's transient extras.
    result = {
        "access_token": token["access_token"],
        "token_type": token.get("token_type", "Bearer"),
        **({"refresh_token": token["refresh_token"]} if token.get("refresh_token") else {}),
    }
    if token.get("expires_at") is not None:
        result["expires_at"] = token["expires_at"]
    elif default_ttl is not None:
        result["expires_at"] = time.time() + default_ttl
    else:
        result["expires_at"] = None
    return result
