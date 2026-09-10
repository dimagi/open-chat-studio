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

    Stateless: trusted server-side code (bound-session pages, the renewal endpoint) re-derives
    it for any session at any time, and each re-derivation starts a fresh lifetime.
    """
    token, _expires_at = issue_session_token_with_expiry(session)
    return token


def issue_session_token_with_expiry(session: ExperimentSession) -> tuple[str, datetime]:
    """`issue_session_token`, also returning the instant the token stops working."""
    expires_at = timezone.now() + session_token_lifetime(session)
    # Whole seconds in the claim, so the returned instant is exactly what the claim says.
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

    Fixed at issuance: activity does not extend a token, and a later change to the channel's
    lifetime does not shorten one already out. The `exp` claim decides; a payload without one
    (minted before the claim was added) falls back to the session's age against the lifetime.
    """
    exp = token_payload.get("exp")
    if exp is not None:
        return timezone.now().timestamp() > exp
    return timezone.now() - session.created_at > session_token_lifetime(session)
