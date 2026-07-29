from rest_framework.permissions import BasePermission

from apps.oauth.permissions import is_client_credentials_request


class CanViewUsage(BasePermission):
    """Usage figures are aggregates over chat messages, so gate the endpoint on the read permission
    for the underlying data: a caller may see usage only if they may view chat messages.

    ``has_perm`` resolves against the team the token is scoped to (the auth layer calls
    ``set_current_team``), and applies to every auth type — including API keys, which the OAuth
    scope check alone does not gate.

    Client-credentials (machine) tokens have no user, so authorization is delegated to the OAuth
    scope (usage:read), enforced by TokenHasOAuthResourceScope.
    """

    def has_permission(self, request, view):
        if is_client_credentials_request(request):
            return True
        return bool(request.user and request.user.has_perm("chat.view_chatmessage"))
