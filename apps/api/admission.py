"""Typed facts about the credential that admitted a Chat API request."""

from dataclasses import dataclass
from enum import StrEnum

from apps.api.exceptions import ChatAdmissionError
from apps.api.models import UserAPIKey
from apps.channels.models import ChannelPlatform, ExperimentChannel, WidgetAuthLevel
from apps.oauth.models import OAuth2AccessToken
from apps.oauth.permissions import is_client_credentials_token
from apps.utils.rate_limit import client_ip


class ChatCredentialKind(StrEnum):
    """Credential mechanisms that can reach the Chat API."""

    ANONYMOUS = "anonymous"
    EMBED_KEY = "embed_key"
    OAUTH_CLIENT_CREDENTIALS = "oauth_client_credentials"
    OAUTH_AUTHORIZATION_CODE = "oauth_authorization_code"
    SESSION_TOKEN = "session_token"
    USER_SESSION = "user_session"
    API_KEY = "api_key"


class ChatEntryPoint(StrEnum):
    """Chat API operation families relevant to admission policy."""

    START_SESSION = "start_session"
    CONTINUE_SESSION = "continue_session"
    RENEW_SESSION_TOKEN = "renew_session_token"


class ChatCapability(StrEnum):
    """Actions granted by the resolved Chat API entry point."""

    START_SESSION = "start_session"
    CONTINUE_SESSION = "continue_session"
    RENEW_SESSION_TOKEN = "renew_session_token"
    ISSUE_SESSION_TOKEN = "issue_session_token"


class SessionTokenPolicy(StrEnum):
    """Session-token behavior established by the admitted Channel policy."""

    ISSUE_AND_REQUIRE = "issue_and_require"
    DO_NOT_ISSUE = "do_not_issue"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ChatAdmission:
    """The immutable authority facts established for one Chat API request."""

    credential_kind: ChatCredentialKind
    entry_point: ChatEntryPoint
    channel: ExperimentChannel | None
    principal: object | None
    team: object | None
    capabilities: frozenset[ChatCapability]
    throttle_bucket: tuple[str, str]
    session_token_policy: SessionTokenPolicy


def resolve_chat_admission(
    request,
    *,
    entry_point: ChatEntryPoint,
    session=None,
    session_token_validated: bool = False,
) -> ChatAdmission:
    """Resolve already-authenticated request attributes into one Chat API admission fact.

    The resolver never reads or validates a credential header. Authentication classes and session
    permissions remain responsible for proving credentials; callers set ``session_token_validated``
    only after the existing session-token check succeeds. This keeps the result from silently
    re-authenticating a credential through a second code path.
    """
    try:
        entry_point = ChatEntryPoint(entry_point)
    except ValueError as error:
        raise ChatAdmissionError("Unknown Chat API entry point") from error

    if session is None:
        session = getattr(request, "chat_session", None)

    auth = getattr(request, "auth", None)
    authenticator = getattr(request, "successful_authenticator", None)
    validated_oauth_token = getattr(request, "chat_oauth_token", None)
    credential_kind, channel, principal, team = _resolve_credential(
        request,
        auth=auth,
        authenticator=authenticator,
        session=session,
        session_token_validated=session_token_validated,
        validated_oauth_token=validated_oauth_token,
    )

    return ChatAdmission(
        credential_kind=credential_kind,
        entry_point=entry_point,
        channel=channel,
        principal=principal,
        team=team,
        capabilities=_capabilities(entry_point, channel),
        throttle_bucket=_throttle_bucket(request, entry_point, channel, auth, session),
        session_token_policy=_session_token_policy(channel),
    )


def _resolve_session_token_credential(session):
    """Resolve a session token after its caller has completed validation."""
    if session is None:
        raise ChatAdmissionError("Validated session-token admission requires a session")
    return ChatCredentialKind.SESSION_TOKEN, session.experiment_channel, None, session.team


def _resolve_credential(
    request,
    *,
    auth,
    authenticator,
    session,
    session_token_validated: bool,
    validated_oauth_token,
) -> tuple[ChatCredentialKind, ExperimentChannel | None, object | None, object | None]:
    """Resolve credential identity and tenancy from DRF's already-authenticated objects."""
    if session_token_validated:
        return _resolve_session_token_credential(session)

    credential_kind = getattr(authenticator, "chat_credential_kind", None)
    if credential_kind in {ChatCredentialKind.EMBED_KEY, ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS}:
        return _resolve_marked_credential(
            credential_kind,
            auth=auth,
            session=session,
            validated_oauth_token=validated_oauth_token,
        )
    return _resolve_unmarked_credential(request, auth=auth, session=session)


def _resolve_marked_credential(
    credential_kind: ChatCredentialKind,
    *,
    auth,
    session,
    validated_oauth_token,
) -> tuple[ChatCredentialKind, ExperimentChannel | None, object | None, object | None]:
    """Resolve credentials identified by a Chat API authentication adapter."""
    if credential_kind == ChatCredentialKind.EMBED_KEY:
        return _resolve_embed_key_credential(auth)

    return _resolve_marked_oauth_credential(auth, session, validated_oauth_token)


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
    """Return the OAuth application for a validated client-credentials token, when present."""
    if isinstance(validated_oauth_token, OAuth2AccessToken) and is_client_credentials_token(validated_oauth_token):
        return validated_oauth_token.application
    return None


def _resolve_unmarked_credential(
    request,
    *,
    auth,
    session,
) -> tuple[ChatCredentialKind, ExperimentChannel | None, object | None, object | None]:
    """Resolve ordinary API, OAuth, user-session, and anonymous credentials."""
    if isinstance(auth, OAuth2AccessToken):
        return _resolve_unmarked_oauth_credential(auth, session)

    if isinstance(auth, UserAPIKey):
        return ChatCredentialKind.API_KEY, None, auth.user, auth.team

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return _resolve_user_session_credential(request, user)

    if auth is None:
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


def _capabilities(entry_point: ChatEntryPoint, channel: ExperimentChannel | None) -> frozenset[ChatCapability]:
    """Return capabilities implied by the operation and Channel's token policy."""
    capabilities = {
        {
            ChatEntryPoint.START_SESSION: ChatCapability.START_SESSION,
            ChatEntryPoint.CONTINUE_SESSION: ChatCapability.CONTINUE_SESSION,
            ChatEntryPoint.RENEW_SESSION_TOKEN: ChatCapability.RENEW_SESSION_TOKEN,
        }[entry_point]
    }
    if _issues_session_token(entry_point, channel):
        capabilities.add(ChatCapability.ISSUE_SESSION_TOKEN)
    return frozenset(capabilities)


def _issues_session_token(entry_point: ChatEntryPoint, channel: ExperimentChannel | None) -> bool:
    """Return whether this entry point must issue a session token for the Channel."""
    return (
        entry_point in {ChatEntryPoint.START_SESSION, ChatEntryPoint.RENEW_SESSION_TOKEN}
        and _session_token_policy(channel) == SessionTokenPolicy.ISSUE_AND_REQUIRE
    )


def _session_token_policy(channel: ExperimentChannel | None) -> SessionTokenPolicy:
    """Translate the durable Channel auth level into the session-token policy."""
    if channel is None:
        return SessionTokenPolicy.NOT_APPLICABLE
    return {
        None: SessionTokenPolicy.ISSUE_AND_REQUIRE,
        WidgetAuthLevel.SESSION_TOKEN: SessionTokenPolicy.ISSUE_AND_REQUIRE,
    }.get(channel.required_auth_level, SessionTokenPolicy.DO_NOT_ISSUE)


def _throttle_bucket(request, entry_point, channel, auth, session) -> tuple[str, str]:
    """Select the existing Chat API throttle identity without inspecting raw credentials."""
    if entry_point == ChatEntryPoint.CONTINUE_SESSION:
        return _session_throttle_bucket(request, session)

    if entry_point == ChatEntryPoint.RENEW_SESSION_TOKEN and isinstance(auth, OAuth2AccessToken):
        return "oauth_client", str(auth.application_id)

    if channel is not None:
        return _channel_throttle_bucket(request, channel)

    return "ip", client_ip(request)


def _session_throttle_bucket(request, session) -> tuple[str, str]:
    """Select a session throttle identity from the resolved session or URL context."""
    if session_id := getattr(session, "external_id", None):
        return "session", str(session_id)
    parser_context = getattr(request, "parser_context", {})
    kwargs = parser_context.get("kwargs", {}) if isinstance(parser_context, dict) else {}
    if kwargs.get("session_id") is not None:
        return "session", str(kwargs["session_id"])
    return "ip", client_ip(request)


def _channel_throttle_bucket(request, channel) -> tuple[str, str]:
    """Select a public-IP or channel throttle identity for a resolved Channel."""
    if channel.platform == ChannelPlatform.PUBLIC:
        return "ip", client_ip(request)
    return "channel", str(channel.pk)
