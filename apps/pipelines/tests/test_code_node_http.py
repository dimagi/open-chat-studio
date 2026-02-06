from unittest.mock import patch

import httpx
import pytest

from apps.pipelines.exceptions import CodeNodeRunError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.nodes import CodeNode
from apps.utils.factories.service_provider_factories import AuthProviderFactory
from apps.utils.factories.team import TeamFactory

_real_httpx_client = httpx.Client


def _patch_transport(handler):
    """Patch httpx.Client in restricted_http to use a MockTransport."""

    def fake_client(**kwargs):
        return _real_httpx_client(transport=httpx.MockTransport(handler))

    return patch("apps.utils.restricted_http.httpx.Client", side_effect=fake_client)


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
    def test_get_request(self, mock_validate):
        code = """
def main(input, **kwargs):
    response = http.get("https://api.example.com/data")
    return str(response["json"]["message"])
"""

        def handler(request):
            return httpx.Response(
                200,
                content=b'{"message": "hello"}',
                headers={"content-type": "application/json"},
            )

        with _patch_transport(handler):
            result = _run_code_node(code)
            assert result.update["messages"][-1] == "hello"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_post_request_with_json(self, mock_validate):
        code = """
def main(input, **kwargs):
    response = http.post("https://api.example.com/data", json={"name": input})
    return str(response["status_code"])
"""

        def handler(request):
            return httpx.Response(201, content=b'{"id": 1}', headers={"content-type": "application/json"})

        with _patch_transport(handler):
            result = _run_code_node(code)
            assert result.update["messages"][-1] == "201"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_error_handling_in_code(self, mock_validate):
        """User code can check is_error and handle HTTP errors."""
        code = """
def main(input, **kwargs):
    response = http.get("https://api.example.com/data")
    if response["is_error"]:
        return f"Error: {response['status_code']}"
    return "Success"
"""

        def handler(request):
            return httpx.Response(404, content=b"not found")

        with _patch_transport(handler):
            result = _run_code_node(code)
            assert result.update["messages"][-1] == "Error: 404"


@pytest.mark.django_db()
class TestCodeNodeHttpWithAuth:
    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_auth_provider_integration(self, mock_validate):
        """End-to-end test: CodeNode uses auth provider for authenticated request."""
        from unittest.mock import MagicMock

        team = TeamFactory()
        AuthProviderFactory(
            name="My API Key",
            type="api_key",
            config={"key": "X-Api-Key", "value": "secret123"},
            team=team,
        )

        # Create a minimal mock experiment session with team
        session = MagicMock()
        session.team = team

        code = """
def main(input, **kwargs):
    response = http.get("https://api.example.com/data", auth="My API Key")
    return str(response["json"]["authenticated"])
"""
        captured_headers = {}

        def handler(request):
            captured_headers.update(dict(request.headers))
            return httpx.Response(
                200,
                content=b'{"authenticated": true}',
                headers={"content-type": "application/json"},
            )

        with _patch_transport(handler):
            result = _run_code_node(code, experiment_session=session)
            assert result.update["messages"][-1] == "True"
            assert captured_headers.get("x-api-key") == "secret123"

    @patch("apps.utils.restricted_http.validate_user_input_url")
    def test_auth_provider_not_found_error(self, mock_validate):
        """AuthProvider not found surfaces as a CodeNodeRunError."""
        from unittest.mock import MagicMock

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
    @patch(
        "apps.utils.restricted_http._get_setting",
        side_effect=lambda name, default: 2 if name == "RESTRICTED_HTTP_MAX_REQUESTS" else default,
    )
    def test_request_limit_error_in_code_node(self, mock_setting, mock_validate):
        """Request limit exceeded surfaces as CodeNodeRunError."""
        code = """
def main(input, **kwargs):
    http.get("https://api.example.com/1")
    http.get("https://api.example.com/2")
    http.get("https://api.example.com/3")
    return "should not reach here"
"""

        def handler(request):
            return httpx.Response(200, content=b"ok")

        with _patch_transport(handler):
            with pytest.raises(CodeNodeRunError, match="Request limit"):
                _run_code_node(code)
