import uuid

from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed, ParseError

from apps.api.exceptions import ChatApiAccessDenied
from apps.channels.models import ChannelPlatform, CredentialMode, ExperimentChannel
from apps.channels.utils import extract_domain_from_headers, get_experiment_session_cached, validate_domain
from apps.experiments.models import Experiment
from apps.oauth.permissions import validated_machine_token
from apps.teams.utils import set_current_team


def chatbot_id_from_body(request) -> str | None:
    """The `chatbot_id` from a start-session body, validated as a UUID.

    A malformed value raises rather than reading as absent: an authenticator that treated a typo
    as "no chatbot named" would silently hand the request to a different credential path.
    """
    if not hasattr(request, "data") or "chatbot_id" not in request.data:
        return None
    chatbot_id = request.data.get("chatbot_id")
    try:
        uuid.UUID(str(chatbot_id))
    except (ValueError, AttributeError) as err:
        raise ParseError("chatbot_id must be a valid UUID") from err
    return chatbot_id


class EmbeddedWidgetAuthentication(authentication.BaseAuthentication):
    """
    Authentication class for embedded widget requests using X-Embed-Key header.

    This authentication method validates:
    1. The presence of X-Embed-Key header
    2. The experiment channel configuration

    The authenticated request will have:
    - request.auth: The ExperimentChannel object
    - request.user: AnonymousUser (since widgets are unauthenticated)
    """

    def authenticate(self, request):
        """
        Authenticate the request using X-Embed-Key header.

        Returns:
            tuple: (user, auth) where auth is the ExperimentChannel
            None: If X-Embed-Key header is not present (allows other auth methods)

        Raises:
            AuthenticationFailed: If authentication fails
        """
        embed_key = request.headers.get("X-Embed-Key")
        if not embed_key:
            # No embed key present - allow other authentication methods
            return None

        # Get experiment ID from request data or path
        experiment_id = self._get_experiment_id(request)
        if not experiment_id:
            raise ParseError("Experiment ID required for embedded widget authentication")

        # Validate the embed key
        try:
            experiment_channel = ExperimentChannel.objects.select_related("experiment", "team").get(
                experiment__public_id=experiment_id,
                platform=ChannelPlatform.EMBEDDED_WIDGET,
                extra_data__widget_token=embed_key,
                deleted=False,
            )
        except ExperimentChannel.DoesNotExist as e:
            raise AuthenticationFailed("Invalid widget embed key") from e

        return (AnonymousUser(), experiment_channel)

    def _get_experiment_id(self, request):
        """
        Extract experiment_id from request or session

        Returns:
            str: The experiment ID (public_id) or None
        """
        # For POST /api/chat/start/ - experiment_id is in request body as chatbot_id
        if chatbot_id := chatbot_id_from_body(request):
            return chatbot_id

        if session_id := request.parser_context["kwargs"].get("session_id"):
            if session := get_experiment_session_cached(session_id):
                return session.experiment.public_id
            else:
                raise AuthenticationFailed("Session does not exist")

        return None

    def authenticate_header(self, request):
        """
        Return the authentication scheme for 401 responses.
        """
        return "X-Embed-Key"


def embed_key_authorizes_channel(request, channel: ExperimentChannel | None) -> bool:
    """Whether this request's X-Embed-Key proves access to `channel`.

    `EmbeddedWidgetAuthentication` only runs when no earlier authenticator matched, so a caller
    that also has a Django session cookie never authenticates as the channel — which is exactly
    the site help widget, embedded in OCS for a logged-in user. This runs the same key and origin
    checks as that class plus `WidgetDomainPermission`, so a view or permission class can treat a
    valid embed key as authorization no matter which class authenticated the request.

    Takes the channel rather than looking it up, so callers that already hold one (a session's
    `experiment_channel`, say) spend no query and are not tied to the channel's experiment being
    the working version.
    """
    embed_key = request.headers.get("X-Embed-Key")
    if not embed_key or channel is None:
        return False
    if channel.platform != ChannelPlatform.EMBEDDED_WIDGET:
        return False
    # Callers that reach a channel by FK traversal (`session.experiment_channel`) bypass the
    # default manager's `deleted=False`, so deleting a widget would otherwise not revoke its key.
    if channel.deleted:
        return False
    if embed_key != channel.extra_data.get("widget_token"):
        return False

    origin_domain = extract_domain_from_headers(request)
    if not origin_domain:
        return False
    return validate_domain(origin_domain, channel.extra_data.get("allowed_domains", []))


def get_embed_key_channel(request, experiment) -> ExperimentChannel | None:
    """Return the widget channel of `experiment` that this request's X-Embed-Key proves access to.

    Returns None (rather than raising) for every failure mode; the caller decides the response.
    """
    embed_key = request.headers.get("X-Embed-Key")
    if not embed_key:
        return None

    channel = (
        ExperimentChannel.objects.select_related("experiment", "team")
        .filter(
            experiment=experiment,
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data__widget_token=embed_key,
        )
        .first()
    )
    return channel if embed_key_authorizes_channel(request, channel) else None


class ChatOAuthAuthentication(authentication.BaseAuthentication):
    """Resolve a client-credentials token to the Chat API Channel it may start a session on.

    Used on `chat_start_session` only, and only at position 0 of that endpoint's authentication
    classes. Both are load-bearing:

    - **Position 0.** DRF stops at the first authenticator that matches, and an existing snippet
      switched to `oauth` mode still sends `X-Embed-Key` (the key is ignored, not removed), so
      `EmbeddedWidgetAuthentication` would otherwise match first and no authenticator would ever
      validate the token. It also decides the status code: `APIView.handle_exception` reads
      `authenticators[0].authenticate_header()`, so a `401` only survives coercion to `403` while
      this class is first.
    - **`start/` only.** On a session-bound request an OAuth-resolved channel in `request.auth`
      would read to `SessionAccessPermission._has_legacy_access` as *an embed key was presented*.

    Returns `(AnonymousUser(), channel)` -- the same shape `EmbeddedWidgetAuthentication` returns,
    which is what puts an `ExperimentChannel` in `request.auth` and so buckets OAuth traffic per
    channel in `ChatAPIRateThrottle` rather than per client IP.
    """

    def authenticate(self, request):
        if not request.headers.get("Authorization"):
            # No token offered: let the Django session / embed key authenticators run.
            return None

        experiment_id = chatbot_id_from_body(request)
        if not experiment_id:
            return None

        experiment = Experiment.objects.filter(public_id=experiment_id, working_version_id__isnull=True).first()
        if experiment is None:
            # Let the view's own get_object_or_404 answer. Chatbot existence is not a secret --
            # `public_id`s ship in every embed snippet -- and a typo'd id must not read as a
            # credential failure, which is the one signal that would tell an integrator otherwise.
            # Version `public_id`s land here too: this door resolves working versions only.
            return None

        channel = self._oauth_channel(experiment)
        if channel is None:
            # The channel is the enablement: nothing admits an OAuth caller until an admin has
            # exposed this chatbot in `oauth` mode.
            raise ChatApiAccessDenied()

        token = validated_machine_token(request, experiment)
        self._check_origin(request, channel)
        request.team = token.team
        set_current_team(token.team)
        return (AnonymousUser(), channel)

    @staticmethod
    def _oauth_channel(experiment) -> ExperimentChannel | None:
        return (
            ExperimentChannel.objects.select_related("experiment", "team")
            .filter(
                experiment=experiment,
                platform=ChannelPlatform.EMBEDDED_WIDGET,
                credential_mode=CredentialMode.OAUTH,
            )
            .first()
        )

    @staticmethod
    def _check_origin(request, channel: ExperimentChannel) -> None:
        """Each credential validates its own origin, and here the domain list decides.

        A blank list means server-only: an originless request is the honest shape for a machine
        integration, and any browser request is refused. A non-blank list declares the channel
        browser-facing, so an originless request is refused exactly as it is under `embed_key` --
        which is what stops a token leaked from a page being replayed from `curl`, the protection
        the dropped embed-key-*and*-token mode used to provide.
        """
        allowed_domains = channel.extra_data.get("allowed_domains", [])
        origin_domain = extract_domain_from_headers(request)
        if not origin_domain:
            if allowed_domains:
                raise ChatApiAccessDenied()
            return
        if not validate_domain(origin_domain, allowed_domains):
            raise ChatApiAccessDenied()

    def authenticate_header(self, request):
        return 'Bearer realm="api"'


def oauth_resolved_channel(request) -> ExperimentChannel | None:
    """The Chat API Channel this request's bearer token resolved, if any.

    `request.auth` holds an `ExperimentChannel` for both credentials, and callers have to tell them
    apart: the origin rule and the credential mode both turn on *which* credential got in.
    """
    if not isinstance(getattr(request, "auth", None), ExperimentChannel):
        return None
    if not isinstance(getattr(request, "successful_authenticator", None), ChatOAuthAuthentication):
        return None
    return request.auth
