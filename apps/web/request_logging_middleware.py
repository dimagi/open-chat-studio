import logging
import re

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.utils.deprecation import MiddlewareMixin

from apps.audit.middleware import get_audit_transaction_id

logger = logging.getLogger("ocs.request")

# Path prefixes for API-type requests (webhooks, REST API)
API_PATH_PREFIXES = ("/api/", "/channels/")


class RequestLoggingMiddleware(MiddlewareMixin):
    """Log API/webhook requests

    Logs team, chatbot/experiment ID, session ID, and widget version
    for API requests matching configured host patterns.

    Settings:
        REQUEST_LOG_DOMAIN_PATTER: List of regex patterns for hosts to log
    """

    def __init__(self, get_response):
        super().__init__(get_response)
        patterns = getattr(settings, "REQUEST_LOG_DOMAIN_PATTER", None)
        if not patterns:
            raise MiddlewareNotUsed("REQUEST_LOG_DOMAIN_PATTER not configured")
        self._host_patterns = [re.compile(p) for p in patterns]

    def _should_log(self, request) -> bool:
        if not request.path.startswith(API_PATH_PREFIXES):
            return False
        host = request.get_host()
        return any(p.search(host) for p in self._host_patterns)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not self._should_log(request):
            return None

        optional_fields = {
            key: value
            for key, value in {
                "query": request.META.get("QUERY_STRING", ""),
                "team": view_kwargs.get("team_slug"),
                "experiment_id": str(view_kwargs.get("experiment_id")),
                "session_id": str(view_kwargs.get("session_id")),
                "widget_version": request.headers.get("x-ocs-widget-version"),
            }.items()
            if value
        }

        logger.info(
            "ocs_request",
            extra={
                "request_id": get_audit_transaction_id(request),
                "host": request.get_host(),
                "path": request.path,
                "method": request.method,
                **optional_fields,
            },
        )
        return None
