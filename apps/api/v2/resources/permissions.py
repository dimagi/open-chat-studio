"""Permission classes for the resource endpoints."""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.oauth.permissions import is_client_credentials_request


class ReadOnlyForMachineTokens(BasePermission):
    """Refuse writes from client-credentials (machine) tokens.

    A machine application is pinned to some of the team's chatbots, but these resources are shared by all of them.
    """

    message = "Client-credentials tokens cannot modify source material or consent forms."

    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS or not is_client_credentials_request(request)
