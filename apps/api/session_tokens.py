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
    except (signing.BadSignature, ValueError):
        # Forged tokens fail the HMAC check (BadSignature); ValueError fails
        # closed on any decode error, keeping this a total function.
        return False
    return payload.get("sid") == str(session_external_id)


def session_token_expired(session: ExperimentSession) -> bool:
    """A session's token stops working a fixed time after the session was created.

    The lifetime is absolute: activity does not extend it, so an admitted caller's
    access is bounded no matter how much they talk. Once it fires the caller must
    start a new session and be re-admitted under whatever rules apply then.

    The session's channel may override the global, because the modes want different
    values: a mid-conversation restart on a public widget is pure UX cost, while a
    channel exposed for abuse-resistance wants it tight. Null means "use the global" —
    there is no "off", since without the lifetime a session would never expire at all.
    """
    channel = session.experiment_channel
    lifetime = channel.session_token_lifetime if channel else None
    if lifetime is None:
        lifetime = settings.CHAT_SESSION_TOKEN_LIFETIME
    return timezone.now() - session.created_at > lifetime
