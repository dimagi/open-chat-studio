from django.conf import settings

CLIENT_CREDENTIALS_GRANT = "client_credentials"


def access_token_expire_seconds(request) -> int:
    """Lifetime of the access token oauthlib is about to issue for `request`.

    A client-credentials token requested with `chat:start` alone is a widget token, spent immediately
    on `/api/chat/start/`, so it lives briefly (ADR-0063). Everything else keeps the server lifetime.
    """
    if _is_widget_token_request(request):
        return settings.OAUTH_CHAT_START_TOKEN_EXPIRE_SECONDS
    return settings.OAUTH_ACCESS_TOKEN_EXPIRE_SECONDS


def _is_widget_token_request(request) -> bool:
    if getattr(request, "grant_type", None) != CLIENT_CREDENTIALS_GRANT:
        return False
    return set(request.scopes or ()) == {settings.CHAT_API_SCOPE}
