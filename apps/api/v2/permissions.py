"""Permission classes shared by the v2 chatbot endpoints."""

from rest_framework.permissions import BasePermission

from apps.oauth.permissions import application_allows_chatbot


class ApplicationAllowsChatbot(BasePermission):
    """Object permission: a machine token reaches only the chatbots its application allows.

    Same allowlist as the chat path, different message: reconfiguring a chatbot is at least as
    sensitive as conversing with it. Creating a chatbot is not gated: a chatbot that does not exist
    yet is on no allowlist.

    Carries no verb logic, so a view that declares it is gated whatever the method; a view whose
    reads are team-scoped (``ChatbotViewSet``, ADR-0028) adds it per write action. Runs through
    ``check_object_permissions``, which ``get_authorized_chatbot`` calls once the chatbot is
    resolved; ``has_permission`` stays True, so OPTIONS and list routes are unaffected.
    """

    message = "This application is not authorized to modify this chatbot."

    def has_object_permission(self, request, view, obj) -> bool:
        return application_allows_chatbot(request, obj)
