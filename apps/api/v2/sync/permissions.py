from rest_framework.permissions import BasePermission

from apps.oauth.permissions import is_client_credentials_request


class CanViewSessions(BasePermission):
    """Session and message sync needs the permission to view sessions.

    Client-credentials (machine) tokens have no user, so they are authorised by the `sessions:read`
    scope (TokenHasOAuthResourceScope) and limited to the chatbots their application reaches.
    """

    def has_permission(self, request, view):
        if is_client_credentials_request(request):
            return True
        return bool(request.user and request.user.has_perm("experiments.view_experimentsession"))
