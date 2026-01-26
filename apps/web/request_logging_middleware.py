import json
import re

import structlog
from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed

from apps.audit.middleware import get_audit_transaction_id

logger = structlog.get_logger("ocs.request")

# Path prefixes for API-type requests (webhooks, REST API)
API_PATH_PREFIXES = ("/api/", "/channels/")


class RequestLoggingMiddleware:
    """Log API/webhook requests

    Logs team, chatbot/experiment ID, session ID, widget version, and response status
    for API requests matching configured host patterns.

    Settings:
        REQUEST_LOG_DOMAIN_PATTER: List of regex patterns for hosts to log
    """

    def __init__(self, get_response):
        self.get_response = get_response
        patterns = getattr(settings, "REQUEST_LOG_DOMAIN_PATTER", None)
        if not patterns:
            raise MiddlewareNotUsed("REQUEST_LOG_DOMAIN_PATTER not configured")
        self._host_patterns = [re.compile(p) for p in patterns]

    def __call__(self, request):
        response = self.get_response(request)
        if self._should_log(request):
            self._log_request(request, response)
        return response

    def _should_log(self, request) -> bool:
        # if not request.path.startswith(API_PATH_PREFIXES):
        #     return False
        host = request.get_host()
        return any(p.search(host) for p in self._host_patterns)

    def _get_post_data(self, request) -> dict:
        """Extract POST data from request body (JSON) or DRF parsed data."""
        if request.method != "POST":
            return {}
        # Prefer DRF's parsed data if available
        if hasattr(request, "data") and isinstance(request.data, dict):
            return request.data
        content_type = request.content_type or ""
        if "application/json" not in content_type:
            return {}
        try:
            return json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return {}

    def _get_field(self, view_kwargs: dict, post_data: dict, *keys: str) -> str | None:
        """Get field from view_kwargs or POST data, trying multiple keys."""
        for key in keys:
            if value := view_kwargs.get(key):
                return str(value)
        for key in keys:
            if value := post_data.get(key):
                return str(value)
        return None

    def _log_request(self, request, response):
        resolver_match = getattr(request, "resolver_match", None)
        view_kwargs = resolver_match.kwargs if resolver_match else {}
        post_data = self._get_post_data(request)

        optional_fields = {
            key: value
            for key, value in {
                "query": request.META.get("QUERY_STRING", ""),
                "team": view_kwargs.get("team_slug"),
                "experiment_id": self._get_field(view_kwargs, post_data, "experiment_id", "chatbot_id"),
                "session_id": self._get_field(view_kwargs, post_data, "session_id"),
                "widget_version": request.headers.get("x-ocs-widget-version"),
            }.items()
            if value
        }

        logger.info(
            "ocs_request",
            request_id=get_audit_transaction_id(request),
            host=request.get_host(),
            path=request.path,
            method=request.method,
            status=response.status_code,
            **optional_fields,
        )
