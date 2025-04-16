import logging
import os

from django.conf import settings
from field_audit.auditors import SystemUserAuditor
from field_audit.models import USER_TYPE_REQUEST

from apps.audit.transaction import get_audit_transaction_id
from apps.teams.utils import get_current_team

log = logging.getLogger("ocs.audit")


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

        if transaction_id := get_audit_transaction_id():
            context["transaction_id"] = transaction_id

        if team := get_current_team():
            context["team"] = team.id
        elif not os.getenv("UNIT_TESTING", False) and _report_missing_team(request):
            # If you see this error in production, do one of the following:
            # - If the error isn't from a view, consider using the `current_team` context manager
            # - If the view is modifying team data, ensure the view is protected by
            #   login_and_team_required decorator and has the 'team_slug' url path parameter
            # - If the view does not modify team data, add the view name to
            #   FIELD_AUDIT_TEAM_EXEMPT_VIEWS in settings
            log.error(  # use `exception` here to include the stack in the event log
                "No team found for audit context",
                extra={"audit_context": context, "view_name": _get_view_name(request)},
            )

        return context


def get_request_context(request):
    username = request.user.username
    context = {
        "user_type": USER_TYPE_REQUEST,
        "username": username,
    }
    if username != request.user.username:
        context["as_username"] = request.user.username
    return context


def _report_missing_team(request):
    return _get_view_name(request) not in settings.FIELD_AUDIT_TEAM_EXEMPT_VIEWS


def _get_view_name(request):
    if resolver_match := getattr(request, "resolver_match", None):
        return resolver_match.view_name
