from datetime import datetime, timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from apps.experiments.models import ExperimentSession

SESSION_TOKEN_SALT = "ocs.chat.session-token"


def session_token_lifetime(session: ExperimentSession) -> timedelta:
    """How long a token issued for `session` lives: the channel's override, else the global."""
    channel = session.experiment_channel
    lifetime = channel.session_token_lifetime if channel else None
    if lifetime is None:
        lifetime = settings.CHAT_SESSION_TOKEN_LIFETIME
    return lifetime


def issue_session_token(session: ExperimentSession) -> str:
    """Mint a signed token proving possession of `session`, expiring `session_token_lifetime` from now.

    Stateless: server-side code may re-issue it for any session, and each issue starts a fresh lifetime.
    """
    token, _expires_at = issue_session_token_with_expiry(session)
    return token


def issue_session_token_with_expiry(session: ExperimentSession) -> tuple[str, datetime]:
    """`issue_session_token`, also returning the instant the token stops working."""
    expires_at = timezone.now() + session_token_lifetime(session)
    # The claim holds whole seconds; return the same instant.
    expires_at = expires_at.replace(microsecond=0)
    payload = {"sid": str(session.external_id), "exp": int(expires_at.timestamp())}
    return signing.dumps(payload, salt=SESSION_TOKEN_SALT), expires_at


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


def session_token_expired(session: ExperimentSession, token_payload: dict) -> bool:
    """Whether the token behind `token_payload` has lapsed.

    The `exp` claim decides. Tokens issued before the claim existed have none; for those the
    session's age against the lifetime stands in.
    """
    exp = token_payload.get("exp")
    if exp is not None:
        return timezone.now().timestamp() > exp
    return timezone.now() - session.created_at > session_token_lifetime(session)
