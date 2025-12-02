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
    1. The experiment channel configuration and allowed domains

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

        origin_domain = extract_domain_from_headers(request)
        if not origin_domain:
            raise AuthenticationFailed("Origin or Referer header required for embedded widgets")

        allowed_domains = experiment_channel.extra_data.get("allowed_domains", [])
        if not validate_domain(origin_domain, allowed_domains):
            raise AuthenticationFailed("Domain not allowed")

        return (AnonymousUser(), experiment_channel)

    def _get_experiment_id(self, request):
        """
        Extract experiment_id from request or session

        Returns:
            str: The experiment ID (public_id) or None
        """
        # For POST /api/chat/start/ - experiment_id is in request body as chatbot_id
        if hasattr(request, "data") and "chatbot_id" in request.data:
            return request.data.get("chatbot_id")

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
