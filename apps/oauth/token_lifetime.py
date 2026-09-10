from django.conf import settings


def access_token_expire_seconds(request) -> int:
    """Lifetime of the access token oauthlib is about to issue for `request`.

    A token requested with `chat:start` alone is bound for a browser: the integration docs tell hosts
    to request exactly that scope for any token handed to the chat widget, and the widget fetches it
    immediately before calling `/api/chat/start/`. Everything else keeps the server-to-server lifetime.
    """
    if set(request.scopes or ()) == {settings.CHAT_API_SCOPE}:
        return settings.OAUTH_CHAT_START_TOKEN_EXPIRE_SECONDS
    return settings.OAUTH_ACCESS_TOKEN_EXPIRE_SECONDS
