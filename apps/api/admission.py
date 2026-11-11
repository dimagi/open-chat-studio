"""Typed facts about the credential that admitted a Chat API request."""

from dataclasses import dataclass
from enum import StrEnum

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


class ChatAdmissionError(ValueError):
    """Raised when validated request attributes cannot form one unambiguous admission."""


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
    validated_embed_key_channel: ExperimentChannel | None = None,
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
        validated_embed_key_channel=validated_embed_key_channel,
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


def _resolve_credential(
    request,
    *,
    auth,
    authenticator,
    session,
    session_token_validated: bool,
    validated_oauth_token,
    validated_embed_key_channel: ExperimentChannel | None,
) -> tuple[ChatCredentialKind, ExperimentChannel | None, object | None, object | None]:
    """Resolve credential identity and tenancy from DRF's already-authenticated objects."""
    if session_token_validated:
        if session is None:
            raise ChatAdmissionError("Validated session-token admission requires a session")
        return ChatCredentialKind.SESSION_TOKEN, session.experiment_channel, None, session.team

    if validated_embed_key_channel is not None:
        return (
            ChatCredentialKind.EMBED_KEY,
            validated_embed_key_channel,
            None,
            validated_embed_key_channel.team,
        )

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
        if not isinstance(auth, ExperimentChannel):
            raise ChatAdmissionError("Embed Key admission has no Chat API Channel")
        return credential_kind, auth, None, auth.team

    principal = None
    if isinstance(auth, ExperimentChannel):
        if isinstance(validated_oauth_token, OAuth2AccessToken) and is_client_credentials_token(validated_oauth_token):
            principal = validated_oauth_token.application
        return credential_kind, auth, principal, auth.team

    if isinstance(auth, OAuth2AccessToken) and is_client_credentials_token(auth):
        return credential_kind, getattr(session, "experiment_channel", None), auth.application, auth.team

    raise ChatAdmissionError("OAuth admission has no validated client credential")


def _resolve_unmarked_credential(
    request,
    *,
    auth,
    session,
) -> tuple[ChatCredentialKind, ExperimentChannel | None, object | None, object | None]:
    """Resolve ordinary API, OAuth, user-session, and anonymous credentials."""
    if isinstance(auth, OAuth2AccessToken):
        if is_client_credentials_token(auth):
            credential_kind = ChatCredentialKind.OAUTH_CLIENT_CREDENTIALS
            principal = auth.application
        else:
            credential_kind = ChatCredentialKind.OAUTH_AUTHORIZATION_CODE
            principal = auth.user
        return credential_kind, getattr(session, "experiment_channel", None), principal, auth.team

    if isinstance(auth, UserAPIKey):
        return ChatCredentialKind.API_KEY, None, auth.user, auth.team

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return ChatCredentialKind.USER_SESSION, None, user, getattr(request, "team", None)

    if auth is None:
        return ChatCredentialKind.ANONYMOUS, None, None, None

    raise ChatAdmissionError("Unsupported or ambiguous Chat API credential")


def get_chat_admission(
    request,
    *,
    entry_point: ChatEntryPoint,
    session=None,
    session_token_validated: bool = False,
    validated_embed_key_channel: ExperimentChannel | None = None,
) -> ChatAdmission:
    """Return the request's cached admission or resolve it once for this policy context."""
    entry_point = ChatEntryPoint(entry_point)
    session = session if session is not None else getattr(request, "chat_session", None)
    session_cache_key = _session_cache_key(session)
    cache_context = (entry_point, session_cache_key, session_token_validated, validated_embed_key_channel)
    cached = getattr(request, "chat_admission", None)
    if cached is not None and getattr(request, "_chat_admission_context", None) == cache_context:
        return cached

    admission = resolve_chat_admission(
        request,
        entry_point=entry_point,
        session=session,
        session_token_validated=session_token_validated,
        validated_embed_key_channel=validated_embed_key_channel,
    )
    request.chat_admission = admission
    request._chat_admission_context = cache_context
    return admission


def _session_cache_key(session) -> tuple[str, object] | None:
    """Return a stable request-local key for the session context used in admission."""
    if session is None:
        return None
    if (session_pk := getattr(session, "pk", None)) is not None:
        return "pk", session_pk
    if (external_id := getattr(session, "external_id", None)) is not None:
        return "external_id", external_id
    return "object", id(session)


def chat_entry_point_for_request(request) -> ChatEntryPoint:
    """Map the stable Chat API URL suffix to the admission operation family."""
    path = getattr(request, "path", "").rstrip("/")
    if path.endswith("/start"):
        return ChatEntryPoint.START_SESSION
    if path.endswith("/token"):
        return ChatEntryPoint.RENEW_SESSION_TOKEN
    return ChatEntryPoint.CONTINUE_SESSION


def _capabilities(entry_point: ChatEntryPoint, channel: ExperimentChannel | None) -> frozenset[ChatCapability]:
    """Return capabilities implied by the operation and Channel's token policy."""
    capabilities = {
        {
            ChatEntryPoint.START_SESSION: ChatCapability.START_SESSION,
            ChatEntryPoint.CONTINUE_SESSION: ChatCapability.CONTINUE_SESSION,
            ChatEntryPoint.RENEW_SESSION_TOKEN: ChatCapability.RENEW_SESSION_TOKEN,
        }[entry_point]
    }
    if (
        entry_point in {ChatEntryPoint.START_SESSION, ChatEntryPoint.RENEW_SESSION_TOKEN}
        and _session_token_policy(channel) == SessionTokenPolicy.ISSUE_AND_REQUIRE
    ):
        capabilities.add(ChatCapability.ISSUE_SESSION_TOKEN)
    return frozenset(capabilities)


def _session_token_policy(channel: ExperimentChannel | None) -> SessionTokenPolicy:
    """Translate the durable Channel auth level into the session-token policy."""
    if channel is None:
        return SessionTokenPolicy.NOT_APPLICABLE
    if channel.required_auth_level in (None, WidgetAuthLevel.SESSION_TOKEN):
        return SessionTokenPolicy.ISSUE_AND_REQUIRE
    return SessionTokenPolicy.DO_NOT_ISSUE


def _throttle_bucket(request, entry_point, channel, auth, session) -> tuple[str, str]:
    """Select the existing Chat API throttle identity without inspecting raw credentials."""
    if entry_point == ChatEntryPoint.CONTINUE_SESSION:
        if session_id := getattr(session, "external_id", None):
            return "session", str(session_id)
        parser_context = getattr(request, "parser_context", {})
        kwargs = parser_context.get("kwargs", {}) if isinstance(parser_context, dict) else {}
        if kwargs.get("session_id") is not None:
            return "session", str(kwargs["session_id"])

    if entry_point == ChatEntryPoint.RENEW_SESSION_TOKEN and isinstance(auth, OAuth2AccessToken):
        return "oauth_client", str(auth.application_id)

    if channel is not None:
        if channel.platform == ChannelPlatform.PUBLIC:
            return "ip", client_ip(request)
        return "channel", str(channel.pk)

    return "ip", client_ip(request)
