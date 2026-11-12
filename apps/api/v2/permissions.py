"""Permission classes shared by the v2 chatbot endpoints."""

from rest_framework.permissions import BasePermission

from apps.oauth.permissions import application_allows_chatbot


class ApplicationAllowsChatbot(BasePermission):
    """Object permission: a machine token reaches only the chatbots its application allows (ADR-0056).

    The allowlist is the chat path's; the message differs because what is refused here is a chatbot's
    configuration, not a conversation. Creating a chatbot is not gated: a chatbot that does not exist
    yet is on no allowlist.

    Carries no verb logic, so a view that declares it is gated whatever the method. Runs through
    ``check_object_permissions``, which DRF's ``get_object`` calls on a retrieve, ``inspect`` calls on
    the version it resolved, and ``get_authorized_chatbot`` calls once a chatbot is resolved for
    writing. ``has_permission`` stays True, so OPTIONS is unaffected, and a list route narrows its
    queryset with ``token_allowed_chatbots`` instead.
    """

    message = "This application is not authorized to access this chatbot."

    def has_object_permission(self, request, view, obj) -> bool:
        return application_allows_chatbot(request, obj)
