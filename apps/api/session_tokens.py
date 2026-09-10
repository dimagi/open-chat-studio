from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from apps.experiments.models import ExperimentSession

SESSION_TOKEN_SALT = "ocs.chat.session-token"


def session_token_lifetime(session: ExperimentSession) -> timedelta:
    """How long a token issued for `session` lives.

    The session's channel may override the global, because the modes want different
    values: a mid-conversation restart on a public widget is pure UX cost, while a
    channel exposed for abuse-resistance wants it tight. Null means "use the global" —
    there is no "off", since without the lifetime a session would never expire at all.
    """
    channel = session.experiment_channel
    lifetime = channel.session_token_lifetime if channel else None
    if lifetime is None:
        lifetime = settings.CHAT_SESSION_TOKEN_LIFETIME
    return lifetime


def issue_session_token(session: ExperimentSession) -> str:
    """Mint a signed token proving possession of `session`, expiring `session_token_lifetime` from now.

    Stateless: the token can be re-derived for any session at any time by trusted
    server-side code (e.g. for bound-session pages), and re-deriving it is how a
    session's access is renewed.
    """
    expires_at = timezone.now() + session_token_lifetime(session)
    return signing.dumps({"sid": str(session.external_id), "exp": int(expires_at.timestamp())}, salt=SESSION_TOKEN_SALT)


def parse_session_token(token: str, session_external_id: str) -> dict | None:
    """The payload of `token` if its signature holds and it was issued for this session, else None."""
    if not token or not isinstance(token, str):
        return None
    try:
        payload = signing.loads(token, salt=SESSION_TOKEN_SALT)
    except (signing.BadSignature, ValueError):
        # Forged tokens fail the HMAC check (BadSignature); ValueError fails
        # closed on any decode error, keeping this a total function.
        return None
    if not isinstance(payload, dict) or payload.get("sid") != str(session_external_id):
        return None
    return payload


def validate_session_token(token: str, session_external_id: str) -> bool:
    """Check `token`'s signature and that it was issued for this session."""
    return parse_session_token(token, session_external_id) is not None


def session_token_expired(session: ExperimentSession, token_payload: dict | None = None) -> bool:
    """Whether the caller's access to `session` has lapsed.

    The lifetime is absolute: activity does not extend it, so an admitted caller's
    access is bounded no matter how much they talk. Once it fires the caller must
    start a new session and be re-admitted under whatever rules apply then.

    A token carrying an `exp` claim expires at that instant. Tokens issued before the
    claim existed have none, and for those the session's age against its lifetime
    stands in.
    """
    exp = (token_payload or {}).get("exp")
    if exp is not None:
        return timezone.now().timestamp() > exp
    return timezone.now() - session.created_at > session_token_lifetime(session)
