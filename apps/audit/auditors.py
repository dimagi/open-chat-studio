import logging

from django.core.exceptions import ValidationError
from field_audit.auditors import BaseAuditor
from field_audit.models import USER_TYPE_REQUEST

from apps.users.models import CustomUser

log = logging.getLogger("audit")


class RequestAuditor(BaseAuditor):
    """Auditor class for getting users from authenticated requests."""

    def change_context(self, request):
        if request is None:
            return None
        if request.user.is_authenticated:
            username = get_request_username(request)
            context = {
                "user_type": USER_TYPE_REQUEST,
                "username": username,
            }
            if username != request.user.username:
                context["as_username"] = request.user.username
            return context
        # short-circuit the audit chain for not-None requests
        return {}


def get_request_username(request):
    hijack_history = request.session.get("hijack_history", [])
    if hijack_history:
        if username := _get_hijack_username(hijack_history):
            return username

    return request.user.username


def _get_hijack_username(hijack_history):
    try:
        pk = CustomUser._meta.pk.to_python(hijack_history[-1])
        return CustomUser.objects.get(pk=pk).username
    except (CustomUser.DoesNotExist, ValidationError) as e:
        log.error("Error getting user from hijack history (%s): %s", hijack_history, str(e))
