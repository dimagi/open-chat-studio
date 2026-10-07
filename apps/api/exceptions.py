from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from apps.moderation.enforcement import BLOCKED_MESSAGE


class EmbeddedWidgetAuthError(Exception):
    """Base exception for embedded widget authentication errors"""

    def __init__(self, message: str):
        self.message = message


class ChatApiAccessDenied(NotAuthenticated):
    """Every admission failure at ``chat/start/`` looks the same from the outside.

    One ``code`` for every reason -- chatbot not exposed, bad token, wrong team, expired
    token, disallowed origin -- so a caller probing for which check failed learns nothing
    the logs don't record properly. A legitimately misconfigured integrator gets no more
    help from the response than an attacker does, which is the trade this makes knowingly.

    The ``401`` itself comes from ``ChatOAuthAuthentication`` sitting first in the
    endpoint's authentication classes: DRF coerces an unauthenticated exception to ``403``
    unless ``authenticators[0].authenticate_header()`` returns a value. This class only
    carries the body, which DRF does not provide -- ``exception_handler`` passes a ``dict``
    detail straight through and the widget's error surface keys on the ``code``.
    """

    default_detail = {"error": "Authentication required to chat with this chatbot", "code": "chat_access_denied"}


class ParticipantBlocked(PermissionDenied):
    """The participant is on the team's denylist, so no session is started and no message is sent."""

    default_detail = {"code": "participant_blocked", "detail": BLOCKED_MESSAGE}
