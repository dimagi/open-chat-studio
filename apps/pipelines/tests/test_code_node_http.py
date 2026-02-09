from unittest.mock import MagicMock, patch

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
