import logging
import os

from django.conf import settings
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
        if request is None:
            context = super().change_context(request)
        elif request.user.is_authenticated:
            context = get_request_context(request)

        if team := get_current_team():
            context["team"] = team.id
        elif not os.getenv("UNIT_TESTING", False) and _report_missing_team(request):
            # If you see this error in production, do one of the following:
            # - If the error isn't from a view, consider using the `current_team` context manager
            # - If the view is modifying team data, ensure the view is protected by
            #   login_and_team_required decorator and has the 'team_slug' url path parameter
            # - If the view does not modify team data, add the view name to
            #   FIELD_AUDIT_TEAM_EXEMPT_VIEWS in settings
            log.error(
                "No team found for audit context",
                extra={"audit_context": context, "view_name": _get_view_name(request)},
            )

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


def _report_missing_team(request):
    return _get_view_name(request) not in settings.FIELD_AUDIT_TEAM_EXEMPT_VIEWS


def _get_view_name(request):
    if resolver_match := getattr(request, "resolver_match", None):
        return resolver_match.view_name
