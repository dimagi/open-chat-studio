import logging

from django.core.exceptions import ValidationError
from field_audit.auditors import SystemUserAuditor
from field_audit.models import USER_TYPE_REQUEST

from apps.teams.utils import get_current_team
from apps.users.models import CustomUser

log = logging.getLogger("audit")


class AuditContextProvider(SystemUserAuditor):
    """Auditor class for getting context for audit event.
    This combines the SystemUserAuditor with the RequestAuditor since we want to include
    'team' context in both cases.
    """

    def change_context(self, request):
        context = {}
        if team := get_current_team():
            context["team"] = team.id

        if request is None:
            context |= super().change_context(request)
        elif request.user.is_authenticated:
            context |= get_request_context(request)

        return context


def get_request_context(request):
    username = get_request_username(request)
    context = {
        "user_type": USER_TYPE_REQUEST,
        "username": username,
    }
    if username != request.user.username:
        context["as_username"] = request.user.username
    return context


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
