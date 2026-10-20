"""Permission classes for the chatbot trigger endpoints."""

from rest_framework import exceptions
from rest_framework.permissions import BasePermission

from apps.oauth.permissions import is_client_credentials_request


class TriggerModelPermission(BasePermission):
    """Require the ``events`` permission for the view's trigger model that the web event views require."""

    actions = {"POST": "add", "PATCH": "change", "DELETE": "delete"}

    def has_permission(self, request, view) -> bool:
        if is_client_credentials_request(request):
            return True
        if request.method == "OPTIONS":
            return True
        action = self.actions.get(request.method)
        if action is None:
            raise exceptions.MethodNotAllowed(request.method)
        opts = view.model._meta
        return bool(request.user and request.user.has_perm(f"{opts.app_label}.{action}_{opts.model_name}"))
