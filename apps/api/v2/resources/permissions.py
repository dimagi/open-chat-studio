"""Permission classes for the resource endpoints."""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.oauth.permissions import token_reaches_all_data


class ReadOnlyForLimitedMachineTokens(BasePermission):
    """Refuse writes from client-credentials (machine) tokens limited to some of the team's chatbots.

    These resources are shared by all of the team's chatbots, so only an application that reaches every
    chatbot may change them.
    """

    message = (
        "Client-credentials tokens can modify source material and consent forms only when their application "
        "has access to all chatbots."
    )

    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS or token_reaches_all_data(request.auth)
