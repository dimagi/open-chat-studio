import time
from typing import TYPE_CHECKING

import httpx
from django.conf import settings
from django.db import transaction
from oauthlib.oauth2 import BackendApplicationClient, OAuth2Error

if TYPE_CHECKING:
    from apps.service_providers.models import AuthProvider

# Refetch this many seconds before the token's real expiry so a token can't
# lapse between the validity check and the request landing at the remote server.
EXPIRY_SKEW_SECONDS = 60

# Fallback lifetime used when the token endpoint omits `expires_in`. Caching for
# a bounded window is better than refetching on every request.
DEFAULT_TOKEN_TTL_SECONDS = 3600


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
        if token and not _is_expired(token):
            return token["access_token"]
        return self._refetch_with_lock()

    def _refetch_with_lock(self) -> str:
        """Fetch a fresh token under a row lock.

        A team-level token is shared across many concurrent Celery pipeline runs.
        On expiry they race to refetch; the lock + re-check ensures exactly one
        token request is made. A short, dedicated transaction is used because the
        caller may be inside a long transaction or on a read replica.
        """
        from apps.service_providers.models import AuthProvider  # noqa: PLC0415 - circular: models imports this module

        with transaction.atomic():
            provider = AuthProvider.objects.select_for_update().get(pk=self.provider.pk)
            if provider._auth_data and not _is_expired(provider._auth_data):
                token = provider._auth_data
            else:
                token = _fetch_client_credentials_token(provider.config)
                provider._auth_data = token
                provider.save(update_fields=["_auth_data"])
            # Keep the in-memory instance in sync so subsequent reads see the token.
            self.provider._auth_data = token
        return token["access_token"]


def _is_expired(token: dict) -> bool:
    expires_at = token.get("expires_at")
    if not expires_at:
        return True
    return time.time() >= (expires_at - EXPIRY_SKEW_SECONDS)


def _fetch_client_credentials_token(config: dict) -> dict:
    """Perform the client-credentials token request and return the token dict."""
    client_id = config["client_id"]
    client_secret = config["client_secret"]
    token_url = config["token_url"]
    scope = config.get("scope") or None
    auth_method = config.get("token_endpoint_auth_method", TokenEndpointAuthMethod.CLIENT_SECRET_BASIC)

    client = BackendApplicationClient(client_id=client_id, scope=scope)

    request_kwargs = {}
    if auth_method == TokenEndpointAuthMethod.CLIENT_SECRET_POST:
        body = client.prepare_request_body(include_client_id=True, client_secret=client_secret)
    else:
        body = client.prepare_request_body()
        request_kwargs["auth"] = httpx.BasicAuth(client_id, client_secret)

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

    try:
        token = client.parse_request_body_response(response.text, scope=scope)
    except OAuth2Error as exc:
        raise OAuthTokenError(f"Invalid OAuth token response from {token_url}: {exc}") from exc

    if not token.get("access_token"):
        raise OAuthTokenError(f"OAuth token response from {token_url} did not contain an access token")

    if not token.get("expires_at"):
        token["expires_at"] = time.time() + DEFAULT_TOKEN_TTL_SECONDS

    # Persist only the fields we need; drop oauthlib's transient extras.
    return {
        "access_token": token["access_token"],
        "token_type": token.get("token_type", "Bearer"),
        "expires_at": token["expires_at"],
    }
