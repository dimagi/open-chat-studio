import logging
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings

from apps.web.request_logging_middleware import RequestLoggingMiddleware


@pytest.fixture()
def get_response():
    response = MagicMock()
    response.status_code = 200
    return MagicMock(return_value=response)


@pytest.fixture()
def middleware(get_response):
    with override_settings(JSON_LOGGING=True):
        return RequestLoggingMiddleware(get_response)


@pytest.fixture()
def request_factory():
    return RequestFactory()


@pytest.mark.django_db()
class TestRequestLoggingMiddleware:
    def test_logs_request_fields(self, middleware, request_factory):
        """Core request fields are logged for every request."""
        request = request_factory.get("/some/path")
        with patch.object(logging.getLogger("ocs.request"), "info") as mock_log:
            middleware(request)

        mock_log.assert_called_once()
        extra = mock_log.call_args.kwargs["extra"]
        assert extra["method"] == "GET"
        assert extra["path"] == "/some/path"
        assert extra["status"] == 200
        # team is added to all log records via apps.utils.logging.ContextVarFilter, not via extra
        assert "team_slug" not in extra

    def test_experiment_id_from_request_attribute(self, middleware, request_factory):
        """experiment_id is taken from request.experiment.public_id when set."""
        request = request_factory.get("/some/path")
        experiment = MagicMock()
        experiment.public_id = "abc-123"
        request.experiment = experiment

        with patch.object(logging.getLogger("ocs.request"), "info") as mock_log:
            middleware(request)

        extra = mock_log.call_args.kwargs["extra"]
        assert extra["experiment_id"] == "abc-123"

    def test_experiment_id_falls_back_to_url_kwargs(self, middleware, request_factory):
        """experiment_id falls back to URL kwargs when request.experiment is not set."""
        request = request_factory.get("/some/path")
        request.resolver_match = MagicMock()
        request.resolver_match.kwargs = {"experiment_id": "from-url"}

        with patch.object(logging.getLogger("ocs.request"), "info") as mock_log:
            middleware(request)

        extra = mock_log.call_args.kwargs["extra"]
        assert extra["experiment_id"] == "from-url"

    def test_request_attribute_takes_priority_over_url_kwargs(self, middleware, request_factory):
        """request.experiment.public_id takes priority over URL kwargs."""
        request = request_factory.get("/some/path")
        experiment = MagicMock()
        experiment.public_id = "from-request"
        request.experiment = experiment
        request.resolver_match = MagicMock()
        request.resolver_match.kwargs = {"experiment_id": "from-url"}

        with patch.object(logging.getLogger("ocs.request"), "info") as mock_log:
            middleware(request)

        extra = mock_log.call_args.kwargs["extra"]
        assert extra["experiment_id"] == "from-request"
