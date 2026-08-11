"""DRF adapter for the shared rate limiting core (#2140 / #2349).

Counting, enforcement, and logging live in apps.utils.rate_limit; this module
resolves the request identity and translates the result into DRF semantics.
"""

from rest_framework.exceptions import Throttled
from rest_framework.throttling import BaseThrottle

from apps.api.models import UserAPIKey
from apps.oauth.models import OAuth2AccessToken
from apps.utils.rate_limit import check, client_ip, is_exempt


class APIRateThrottle(BaseThrottle):
    scope = "api"
    _wait = None

    def allow_request(self, request, view):
        if is_exempt(request):
            return True
        identity_type, identity = self.identity(request)
        team = getattr(request, "team", None)
        result = check(self.scope, identity_type, identity, team_id=team.pk if team else None)
        # Auth attributes live on the DRF Request; the headers middleware reads
        # the underlying HttpRequest, so the result is attached there.
        request._request.rate_limit_result = result
        self._wait = result.retry_after
        return result.allowed

    def wait(self):
        return self._wait

    def identity(self, request) -> tuple[str, str]:
        # `team` may be a lazily-resolved proxy set by TeamsMiddleware on the underlying
        # HttpRequest (present on every request) rather than a plain Team instance set
        # by our auth classes; a truthiness check evaluates it safely instead of raising
        # when the proxy resolves to None.
        team = getattr(request, "team", None)
        if team:
            return "team", str(team.pk)
        auth = getattr(request, "auth", None)
        if isinstance(auth, UserAPIKey):
            return "api_key", str(auth.pk)
        if isinstance(auth, OAuth2AccessToken):
            return "oauth_client", str(auth.application_id)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return "user", str(user.pk)
        return "ip", client_ip(request)


def api_exception_handler(exc, context):
    # circular: rest_framework.views resolves DEFAULT_THROTTLE_CLASSES (this module) at import time
    from rest_framework.views import exception_handler as drf_exception_handler  # noqa: PLC0415

    response = drf_exception_handler(exc, context)
    if isinstance(exc, Throttled) and response is not None:
        response.data = {"detail": "Rate limit exceeded.", "available_in": int(exc.wait or 0)}
    return response
