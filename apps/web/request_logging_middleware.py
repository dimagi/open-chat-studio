import json
import logging
import time

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import RawPostDataException

from apps.audit.transaction import get_audit_transaction_id

logger = logging.getLogger("ocs.request")


class RequestLoggingMiddleware:
    """Log requests

    Logs host, team, chatbot/experiment ID, session ID, widget version, and response status
    for requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        if not settings.JSON_LOGGING:
            raise MiddlewareNotUsed()

    def __call__(self, request):
        start_time = time.perf_counter()
        response = self.get_response(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        self._log_request(request, response, int(duration_ms))
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
            parsed_data = json.loads(request.body)
            # Ensure we return a dict even if the JSON is a list or other type
            if isinstance(parsed_data, dict):
                return parsed_data
            return {}
        except (json.JSONDecodeError, ValueError, RawPostDataException):
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

        extra = {
            "host": request.get_host(),
            "method": request.method,
            "status": response.status_code,
            "path": request.path,
            "request_id": get_audit_transaction_id(),
            "duration": duration_ms,
        }
        # team is added automatically to all log records via apps.utils.logging.ContextVarFilter
        # Webhook views set request.experiment after resolving the channel, since the experiment
        # isn't available in the URL. Fall back to URL kwargs / POST data for views that have it
        # in the path (e.g. API views) or body.
        experiment = getattr(request, "experiment", None)
        experiment_id = (
            str(experiment.public_id)
            if experiment
            else self._get_field(view_kwargs, post_data, "experiment_id", "chatbot_id")
        )
        for key, value in {
            "experiment_id": experiment_id,
            "session_id": self._get_field(view_kwargs, post_data, "session_id"),
            "widget_version": request.headers.get("x-ocs-widget-version"),
            "query": request.META.get("QUERY_STRING", ""),
        }.items():
            if value:
                extra[key] = value

        if response.status_code >= 500:
            logger.error("django_request", extra=extra)
        elif response.status_code >= 400:
            logger.warning("django_request", extra=extra)
        else:
            logger.info("django_request", extra=extra)
