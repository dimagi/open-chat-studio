import json
import time

import structlog

from apps.audit.middleware import get_audit_transaction_id

logger = structlog.get_logger("ocs.request")


class RequestLoggingMiddleware:
    """Log requests

    Logs host, team, chatbot/experiment ID, session ID, widget version, and response status
    for requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.perf_counter()
        response = self.get_response(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        self._log_request(request, response, duration_ms)
        return response

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

    def _log_request(self, request, response, duration_ms):
        resolver_match = getattr(request, "resolver_match", None)
        view_kwargs = resolver_match.kwargs if resolver_match else {}
        post_data = self._get_post_data(request)

        optional_fields = {
            key: value
            for key, value in {
                "experiment_id": self._get_field(view_kwargs, post_data, "experiment_id", "chatbot_id"),
                "session_id": self._get_field(view_kwargs, post_data, "session_id"),
                "widget_version": request.headers.get("x-ocs-widget-version"),
                "query": request.META.get("QUERY_STRING", ""),
            }.items()
            if value
        }

        logger_fn = logger.info
        if response.status_code >= 500:
            logger_fn = logger.error
        elif response.status_code >= 400:
            logger_fn = logger.warning
        logger_fn(
            "django_request",
            host=request.get_host(),
            method=request.method,
            status=response.status_code,
            path=request.path,
            request_id=get_audit_transaction_id(request),
            duration=duration_ms,
            **optional_fields,
        )
