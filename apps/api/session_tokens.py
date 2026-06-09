from django.conf import settings
from django.core import signing
from django.utils import timezone

from apps.experiments.models import ExperimentSession

SESSION_TOKEN_SALT = "ocs.chat.session-token"


def issue_session_token(session: ExperimentSession) -> str:
    """Mint a signed token proving possession of `session`.

    Stateless: the token can be re-derived for any session at any time by
    trusted server-side code (e.g. for bound-session pages).
    """
    return signing.dumps({"sid": str(session.external_id)}, salt=SESSION_TOKEN_SALT)


def validate_session_token(token: str, session_external_id: str) -> bool:
    """Check `token`'s signature and that it was issued for this session."""
    if not token or not isinstance(token, str):
        return False
    try:
        payload = signing.loads(token, salt=SESSION_TOKEN_SALT)
    except signing.BadSignature:
        return False
    return payload.get("sid") == str(session_external_id)


def session_token_expired(session: ExperimentSession) -> bool:
    """Sliding inactivity backstop: reject token access to long-inactive sessions.

    Activity is the session's `last_activity_at` (updated on each user message;
    polling does not count, so a leaked token cannot keep a session alive),
    falling back to session creation when there has been no activity yet.
    """
    last_activity = session.last_activity_at or session.created_at
    return timezone.now() - last_activity > settings.CHAT_SESSION_TOKEN_INACTIVITY_WINDOW
