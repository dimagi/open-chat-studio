import uuid

from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed, ParseError

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import extract_domain_from_headers, get_experiment_session_cached, validate_domain


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
        if hasattr(request, "data") and "chatbot_id" in request.data:
            chatbot_id = request.data.get("chatbot_id")
            try:
                uuid.UUID(str(chatbot_id))
            except (ValueError, AttributeError) as err:
                raise ParseError("chatbot_id must be a valid UUID") from err
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
