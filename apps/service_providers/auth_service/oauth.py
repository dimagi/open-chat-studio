import hashlib
import json
import time
from typing import TYPE_CHECKING

import httpx
from django.conf import settings
from django.db import transaction
from oauthlib.oauth2 import BackendApplicationClient, OAuth2Error

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
        return self._refetch_with_lock()

    def _refetch_with_lock(self) -> str:
        """Fetch a fresh token under a row lock.

        A team-level token is shared across many concurrent Celery pipeline runs.
        On expiry they race to refetch; the lock + re-check ensures exactly one
        token request is made. A short, dedicated transaction is used because the
        caller may be inside a long transaction or on a read replica.
        """
        model = type(self.provider)
        with transaction.atomic():
            provider = model.objects.select_for_update().get(pk=self.provider.pk)
            if _token_is_valid(provider._auth_data, provider.config):
                token = provider._auth_data
            else:
                token = _fetch_client_credentials_token(provider.config)
                token["config_fingerprint"] = _config_fingerprint(provider.config)
                provider._auth_data = token
                provider.save(update_fields=["_auth_data"])
            # Keep the in-memory instance in sync so subsequent reads see the token.
            self.provider._auth_data = token
        return token["access_token"]


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
    if not expires_at:
        return True
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
    return _parse_token_response(client, response_text, token_url)


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
        raise OAuthTokenError(f"Failed to fetch OAuth token from {token_url}: {exc}") from exc
    return response.text


def _parse_token_response(client: BackendApplicationClient, response_text: str, token_url: str) -> dict:
    """Parse the token response and normalise it to the fields we persist."""
    try:
        token = client.parse_request_body_response(response_text)
    except OAuth2Error as exc:
        raise OAuthTokenError(f"Invalid OAuth token response from {token_url}: {exc}") from exc

    if not token.get("access_token"):
        raise OAuthTokenError(f"OAuth token response from {token_url} did not contain an access token")

    # Persist only the fields we need; drop oauthlib's transient extras.
    return {
        "access_token": token["access_token"],
        "token_type": token.get("token_type", "Bearer"),
        "expires_at": token.get("expires_at") or (time.time() + DEFAULT_TOKEN_TTL_SECONDS),
    }
