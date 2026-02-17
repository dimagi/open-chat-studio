from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.pipelines.exceptions import CodeNodeRunError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.nodes import CodeNode
from apps.utils.factories.service_provider_factories import AuthProviderFactory
from apps.utils.factories.team import TeamFactory


def _run_code_node(code, experiment_session=None):
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    return node._process(
        PipelineState(
            outputs={},
            experiment_session=experiment_session,
            last_node_input="test_input",
            node_inputs=["test_input"],
        )
    )


class TestCodeNodeHttpAvailability:
    def test_http_global_available(self):
        """The 'http' global should be available in CodeNode."""
        code = """
def main(input, **kwargs):
    return "has_http" if http else "no_http"
"""
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "has_http"

    def test_httpx_import_blocked_at_runtime(self):
        """Direct httpx import should be blocked at runtime."""
        code = """
def main(input, **kwargs):
    import httpx
    return "should not reach here"
"""
        node = CodeNode(name="test", node_id="123", django_node=None, code=code)
        with pytest.raises(CodeNodeRunError, match="Importing 'httpx' is not allowed"):
            node._process(PipelineState(outputs={}, experiment_session=None, last_node_input="hi", node_inputs=["hi"]))


@pytest.mark.django_db()
class TestCodeNodeHttpRequests:
    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_get_request(self, mock_validate, httpx_mock):
        code = """
def main(input, **kwargs):
    response = http.get("https://api.example.com/data")
    return str(response["json"]["message"])
"""
        httpx_mock.add_response(json={"message": "hello"})
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "hello"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_post_request_with_json(self, mock_validate, httpx_mock):
        code = """
def main(input, **kwargs):
    response = http.post("https://api.example.com/data", json={"name": input})
    return str(response["status_code"])
"""
        httpx_mock.add_response(status_code=201, json={"id": 1})
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "201"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_error_handling_in_code(self, mock_validate, httpx_mock):
        """User code can check is_error and handle HTTP errors."""
        code = """
def main(input, **kwargs):
    response = http.get("https://api.example.com/data")
    if response["is_error"]:
        return f"Error: {response['status_code']}"
    return "Success"
"""
        httpx_mock.add_response(status_code=404, content=b"not found")
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "Error: 404"


@pytest.mark.django_db()
class TestCodeNodeHttpWithAuth:
    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_auth_provider_integration(self, mock_validate, httpx_mock):
        """End-to-end test: CodeNode uses auth provider for authenticated request."""
        team = TeamFactory()
        AuthProviderFactory(
            name="My API Key",
            type="api_key",
            config={"key": "X-Api-Key", "value": "secret123"},
            team=team,
        )

        session = MagicMock()
        session.team = team

        code = """
def main(input, **kwargs):
    response = http.get("https://api.example.com/data", auth="My API Key")
    return str(response["json"]["authenticated"])
"""
        httpx_mock.add_response(json={"authenticated": True})
        result = _run_code_node(code, experiment_session=session)
        assert result.update["messages"][-1] == "True"
        request = httpx_mock.get_request()
        assert request.headers.get("x-api-key") == "secret123"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_auth_provider_not_found_error(self, mock_validate):
        """AuthProvider not found surfaces as a CodeNodeRunError."""
        team = TeamFactory()
        session = MagicMock()
        session.team = team

        code = """
def main(input, **kwargs):
    response = http.get("https://api.example.com/data", auth="Nonexistent")
    return "should not reach here"
"""
        with pytest.raises(CodeNodeRunError, match="not found"):
            _run_code_node(code, experiment_session=session)


@pytest.mark.django_db()
class TestCodeNodeHttpLimits:
    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_request_limit_error_in_code_node(self, mock_validate, httpx_mock, settings):
        """Request limit exceeded surfaces as CodeNodeRunError."""
        settings.RESTRICTED_HTTP_MAX_REQUESTS = 2
        code = """
def main(input, **kwargs):
    http.get("https://api.example.com/1")
    http.get("https://api.example.com/2")
    http.get("https://api.example.com/3")
    return "should not reach here"
"""
        httpx_mock.add_response(content=b"ok")
        httpx_mock.add_response(content=b"ok")
        with pytest.raises(CodeNodeRunError, match="Request limit"):
            _run_code_node(code)


@pytest.mark.django_db()
class TestCodeNodeHttpErrorHandling:
    """Tests that exception classes exposed on the http client can be caught
    inside the restricted Python sandbox using try/except blocks."""

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_catch_timeout_error(self, mock_validate, httpx_mock):
        """Sandbox code can catch http.TimeoutError."""
        code = """
def main(input, **kwargs):
    try:
        response = http.get("https://api.example.com/slow", timeout=1)
        return "should not reach here"
    except http.TimeoutError:
        return "caught timeout"
"""
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"))
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "caught timeout"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_catch_connection_error(self, mock_validate, httpx_mock):
        """Sandbox code can catch http.ConnectionError."""
        code = """
def main(input, **kwargs):
    try:
        response = http.get("https://api.example.com/down")
        return "should not reach here"
    except http.ConnectionError:
        return "caught connection error"
"""
        for _ in range(3):
            httpx_mock.add_exception(httpx.ConnectError("connection refused"))
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "caught connection error"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_catch_response_too_large(self, mock_validate, httpx_mock, settings):
        """Sandbox code can catch http.ResponseTooLarge."""
        settings.RESTRICTED_HTTP_MAX_RESPONSE_BYTES = 10
        code = """
def main(input, **kwargs):
    try:
        response = http.get("https://api.example.com/huge")
        return "should not reach here"
    except http.ResponseTooLarge:
        return "response too large"
"""
        httpx_mock.add_response(content=b"x" * 100)
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "response too large"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_catch_request_limit_exceeded(self, mock_validate, httpx_mock, settings):
        """Sandbox code can catch http.RequestLimitExceeded."""
        settings.RESTRICTED_HTTP_MAX_REQUESTS = 1
        code = """
def main(input, **kwargs):
    http.get("https://api.example.com/1")
    try:
        http.get("https://api.example.com/2")
        return "should not reach here"
    except http.RequestLimitExceeded:
        return "limit exceeded"
"""
        httpx_mock.add_response(text="ok")
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "limit exceeded"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_catch_request_too_large(self, mock_validate, settings):
        """Sandbox code can catch http.RequestTooLarge."""
        settings.RESTRICTED_HTTP_MAX_REQUEST_BYTES = 10
        code = """
def main(input, **kwargs):
    try:
        http.post("https://api.example.com/data", json={"key": "x" * 100})
        return "should not reach here"
    except http.RequestTooLarge:
        return "request too large"
"""
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "request too large"

    def test_catch_invalid_url(self):
        """Sandbox code can catch http.InvalidURL (SSRF blocked)."""
        code = """
def main(input, **kwargs):
    try:
        http.get("http://127.0.0.1/internal")
        return "should not reach here"
    except http.InvalidURL:
        return "blocked by ssrf"
"""
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "blocked by ssrf"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_catch_auth_provider_error(self, mock_validate):
        """Sandbox code can catch http.AuthProviderError."""
        code = """
def main(input, **kwargs):
    try:
        http.get("https://api.example.com/data", auth="nonexistent")
        return "should not reach here"
    except http.AuthProviderError:
        return "auth provider error"
"""
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "auth provider error"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_catch_base_error_class(self, mock_validate, httpx_mock):
        """http.Error catches all HTTP client exceptions."""
        code = """
def main(input, **kwargs):
    try:
        http.get("https://api.example.com/slow", timeout=1)
        return "should not reach here"
    except http.Error:
        return "caught with base class"
"""
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "caught with base class"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_specific_before_base(self, mock_validate, httpx_mock):
        """Specific exception is caught before the base http.Error."""
        code = """
def main(input, **kwargs):
    try:
        http.get("https://api.example.com/data", timeout=1)
    except http.TimeoutError:
        return "specific: timeout"
    except http.Error:
        return "generic: error"
"""
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "specific: timeout"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_error_message_accessible(self, mock_validate, httpx_mock):
        """Exception message is accessible inside the sandbox."""
        code = """
def main(input, **kwargs):
    try:
        http.get("https://api.example.com/data", timeout=1)
    except http.TimeoutError as exc:
        return f"error: {exc}"
"""
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"))
        result = _run_code_node(code)
        assert "read timed out" in result.update["messages"][-1]

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_recovery_after_error(self, mock_validate, httpx_mock, settings):
        """Sandbox code can catch an error and continue making requests."""
        settings.RESTRICTED_HTTP_MAX_RESPONSE_BYTES = 10
        code = """
def main(input, **kwargs):
    try:
        http.get("https://api.example.com/huge")
    except http.ResponseTooLarge:
        pass
    response = http.get("https://api.example.com/ok")
    return response["text"]
"""
        httpx_mock.add_response(content=b"x" * 100)  # first: too large
        settings.RESTRICTED_HTTP_MAX_RESPONSE_BYTES = 1_048_576
        httpx_mock.add_response(text="recovered")  # second: ok
        result = _run_code_node(code)
        assert result.update["messages"][-1] == "recovered"
