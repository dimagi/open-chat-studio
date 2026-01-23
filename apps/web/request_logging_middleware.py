import logging
import re

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger("ocs.request")

# Path prefixes for API-type requests (webhooks, REST API)
API_PATH_PREFIXES = ("/api/", "/channels/")


class LegacyDomainLoggingMiddleware(MiddlewareMixin):
    """Log API/webhook requests to legacy domains to track migration progress.

    Logs team, chatbot/experiment ID, session ID, and widget version
    for API requests matching configured legacy host patterns.

    Settings:
        LEGACY_DOMAIN_PATTERNS: List of regex patterns for legacy hosts
    """

    def __init__(self, get_response):
        super().__init__(get_response)
        patterns = getattr(settings, "LEGACY_DOMAIN_PATTERNS", None)
        if not patterns:
            raise MiddlewareNotUsed("LEGACY_DOMAIN_PATTERNS not configured")
        self._host_patterns = [re.compile(p) for p in patterns]

    def _should_log(self, request) -> bool:
        if not request.path.startswith(API_PATH_PREFIXES):
            return False
        host = request.get_host()
        return any(p.search(host) for p in self._host_patterns)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not self._should_log(request):
            return None

        logger.info(
            "legacy_domain_request",
            extra={
                "host": request.get_host(),
                "path": request.path,
                "method": request.method,
                "team": view_kwargs.get("team_slug"),
                "experiment_id": str(view_kwargs.get("experiment_id", "")),
                "session_id": str(view_kwargs.get("session_id", "")),
                "widget_version": request.headers.get("x-ocs-widget-version"),
            },
        )
        return None
