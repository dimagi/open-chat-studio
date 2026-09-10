from django.conf import settings

CLIENT_CREDENTIALS_GRANT = "client_credentials"


def access_token_expire_seconds(request) -> int:
    """Lifetime of the access token oauthlib is about to issue for `request`.

    A client-credentials token requested with `chat:start` alone is the shape a host mints for the
    chat widget, which spends it on `/api/chat/start/` straight away, so it lives briefly. The
    door accepts any token carrying `chat:start`, so this holds only for hosts that request that
    scope alone for browser-bound tokens (ADR-0063). Everything else keeps the server lifetime.
    """
    if getattr(request, "grant_type", None) == CLIENT_CREDENTIALS_GRANT and set(request.scopes or ()) == {
        settings.CHAT_API_SCOPE
    }:
        return settings.OAUTH_CHAT_START_TOKEN_EXPIRE_SECONDS
    return settings.OAUTH_ACCESS_TOKEN_EXPIRE_SECONDS
