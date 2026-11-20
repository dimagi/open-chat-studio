"""Resolve validated Chat API credentials into their identity and team context."""

from dataclasses import dataclass
from enum import StrEnum

from apps.api.exceptions import ChatAdmissionError
from apps.api.models import UserAPIKey
from apps.channels.models import ExperimentChannel
from apps.oauth.models import OAuth2AccessToken
from apps.oauth.permissions import is_client_credentials_token


class ChatCredentialKind(StrEnum):
    """Credential mechanisms that can reach the Chat API."""

    ANONYMOUS = "anonymous"
    EMBED_KEY = "embed_key"
    OAUTH_CLIENT_CREDENTIALS = "oauth_client_credentials"
    OAUTH_AUTHORIZATION_CODE = "oauth_authorization_code"
    SESSION_TOKEN = "session_token"
    USER_SESSION = "user_session"
    API_KEY = "api_key"


@dataclass(frozen=True, slots=True)
class CredentialContext:
    """Already-authenticated request facts needed to classify a Chat API credential."""

    request: object
    auth: object | None
    authenticator: object | None
    session: object | None
    session_token_validated: bool
    validated_oauth_token: OAuth2AccessToken | None
    validated_embed_key_channel: ExperimentChannel | None


def resolve_credential(context: CredentialContext):
    """Resolve credential identity and tenancy without reading raw credentials."""
    if context.session_token_validated:
        return _resolve_session_token_credential(context.session)

    if context.validated_embed_key_channel is not None:
        channel = context.validated_embed_key_channel
        return ChatCredentialKind.EMBED_KEY, channel, None, channel.team

    credential_kind = getattr(context.authenticator, "chat_credential_kind", None)
    if credential_kind in {ChatCredentialKind.EMBED_KEY, ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS}:
        return _resolve_marked_credential(credential_kind, context)
    return _resolve_unmarked_credential(context)


def _resolve_session_token_credential(session):
    """Resolve a session token after its caller has completed validation."""
    if session is None:
        raise ChatAdmissionError("Validated session-token admission requires a session")
    return ChatCredentialKind.SESSION_TOKEN, session.experiment_channel, None, session.team


def _resolve_marked_credential(credential_kind: ChatCredentialKind, context: CredentialContext):
    """Resolve credentials identified by a Chat API authentication adapter."""
    if credential_kind == ChatCredentialKind.EMBED_KEY:
        return _resolve_embed_key_credential(context.auth)
    return _resolve_marked_oauth_credential(context.auth, context.session, context.validated_oauth_token)


def _resolve_embed_key_credential(auth):
    """Resolve the Channel supplied by the validated Embed Key adapter."""
    if not isinstance(auth, ExperimentChannel):
        raise ChatAdmissionError("Embed Key admission has no Chat API Channel")
    return ChatCredentialKind.EMBED_KEY, auth, None, auth.team


def _resolve_marked_oauth_credential(auth, session, validated_oauth_token):
    """Resolve an OAuth credential marked by the Chat API authentication adapter."""
    if isinstance(auth, ExperimentChannel):
        _validate_oauth_session_team(session, auth.team)
        principal = _validated_oauth_principal(validated_oauth_token)
        return ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS, auth, principal, auth.team

    if isinstance(auth, OAuth2AccessToken) and is_client_credentials_token(auth):
        _validate_oauth_session_team(session, auth.team)
        return (
            ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS,
            getattr(session, "experiment_channel", None),
            auth.application,
            auth.team,
        )

    raise ChatAdmissionError("OAuth admission has no validated client credential")


def _validated_oauth_principal(validated_oauth_token):
    """Return the validated client-credentials token's OAuth application, if present."""
    if isinstance(validated_oauth_token, OAuth2AccessToken) and is_client_credentials_token(validated_oauth_token):
        return validated_oauth_token.application
    return None


def _resolve_unmarked_credential(context: CredentialContext):
    """Resolve ordinary API, OAuth, user-session, and anonymous credentials."""
    if isinstance(context.auth, OAuth2AccessToken):
        return _resolve_unmarked_oauth_credential(context.auth, context.session)

    if isinstance(context.auth, UserAPIKey):
        return ChatCredentialKind.API_KEY, None, context.auth.user, context.auth.team

    user = getattr(context.request, "user", None)
    if user is not None and user.is_authenticated:
        return _resolve_user_session_credential(context.request, user)

    if context.auth is None:
        return ChatCredentialKind.ANONYMOUS, None, None, None

    raise ChatAdmissionError("Unsupported or ambiguous Chat API credential")


def _resolve_unmarked_oauth_credential(auth, session):
    """Classify an OAuth token that was not marked by a dedicated Chat adapter."""
    _validate_oauth_session_team(session, auth.team)
    if is_client_credentials_token(auth):
        return (
            ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS,
            getattr(session, "experiment_channel", None),
            auth.application,
            auth.team,
        )
    return (
        ChatCredentialKind.OAUTH_AUTHORIZATION_CODE,
        getattr(session, "experiment_channel", None),
        auth.user,
        auth.team,
    )


def _validate_oauth_session_team(session, oauth_team):
    """Reject an OAuth request that combines a token and session from different teams."""
    session_team = getattr(session, "team", None)
    if session_team is not None and session_team != oauth_team:
        raise ChatAdmissionError("OAuth credential and session belong to different teams")


def _resolve_user_session_credential(request, user):
    """Resolve an authenticated browser user as a team-scoped Chat credential."""
    return ChatCredentialKind.USER_SESSION, None, user, getattr(request, "team", None)
