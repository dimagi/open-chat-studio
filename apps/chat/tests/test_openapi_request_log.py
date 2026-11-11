import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from langchain_core.callbacks import BaseCallbackHandler

from apps.chat.agent.openapi_tool import OpenAPIOperationExecutor, openapi_spec_op_to_function_def
from apps.chat.agent.request_log import MAX_BODY_LENGTH, REDACTED
from apps.chat.tests.openapi_helpers import make_openapi_schema
from apps.service_providers.auth_service import (
    ApiKeyAuthService,
    AuthService,
    BasicAuthService,
    BearerTokenAuthService,
    CommCareAuthService,
    anonymous_auth_service,
)
from apps.service_providers.tracing import TracingService
from apps.service_providers.tracing.const import CUSTOM_ACTION_REQUEST_EVENT
from apps.service_providers.tracing.ocs_tracer import OCSTracer
from apps.trace.models import Trace
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.openapi import OpenAPISpec

URL = "https://example.com/test"


class RequestLogCapture(BaseCallbackHandler):
    def __init__(self):
        super().__init__()
        self.logs = []

    def on_custom_event(self, name, data, **kwargs):
        if name == CUSTOM_ACTION_REQUEST_EVENT:
            self.logs.append(data)


@pytest.fixture(autouse=True)
def _no_notifications():
    with (
        patch("apps.chat.agent.openapi_tool.custom_action_api_failure_notification"),
        patch("apps.chat.agent.openapi_tool.custom_action_unexpected_error_notification"),
    ):
        yield


def _build_tool(operation: dict, auth_service: AuthService = anonymous_auth_service, method: str = "get"):
    spec = OpenAPISpec.from_spec_dict(make_openapi_schema(operation, method=method))
    function_def = openapi_spec_op_to_function_def(spec, next(iter(spec.paths)), method)
    return function_def.build_tool(auth_service, custom_action=SimpleNamespace(name="Weather API"))


def _run_tool(operation: dict, args: dict | None = None, **kwargs) -> list[dict]:
    capture = RequestLogCapture()
    _build_tool(operation, **kwargs).run(args or {}, tool_call_id="123", callbacks=[capture])
    return capture.logs


def test_request_and_response_are_logged(httpx_mock):
    body_operation = {
        "requestBody": {
            "content": {"application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}}
        }
    }
    httpx_mock.add_response(url=URL, method="POST", json={"id": 7}, headers={"X-Request-Id": "abc"})

    [log] = _run_tool(body_operation, {"body_data": {"name": "John"}}, method="post")

    assert log["tool"] == "test_post"
    assert log["custom_action"] == "Weather API"
    assert log["method"] == "POST"
    assert log["url"] == URL
    assert log["request_headers"]["content-type"] == "application/json"
    assert log["request_body"] == '{"name":"John"}'
    assert log["status_code"] == 200
    assert log["response_headers"]["x-request-id"] == "abc"
    assert log["response_body"] == '{"id":7}'
    assert isinstance(log["duration_ms"], int)
    assert log["error"] == ""


@pytest.mark.parametrize(
    ("auth_service", "header", "secrets"),
    [
        pytest.param(
            BasicAuthService(username="user", password="pass"),
            "authorization",
            ["Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"],
            id="basic",
        ),
        pytest.param(BearerTokenAuthService(token="tok-123"), "authorization", ["tok-123"], id="bearer"),
        pytest.param(ApiKeyAuthService(key="X-Client", value="key-456"), "x-client", ["key-456"], id="api-key"),
        pytest.param(
            CommCareAuthService(username="user", api_key="ck-789"),
            "authorization",
            ["ApiKey user:ck-789", "user:ck-789"],
            id="commcare",
        ),
    ],
)
def test_auth_headers_are_redacted_everywhere(httpx_mock, auth_service, header, secrets):
    """An API that echoes the credential back does not get it into the log."""

    def echo_headers(request: httpx.Request) -> httpx.Response:
        echoed = dict(request.headers)
        return httpx.Response(200, json={"headers": echoed}, headers={"X-Echo": echoed[header]})

    httpx_mock.add_callback(echo_headers, url=URL)

    [log] = _run_tool({}, auth_service=auth_service)

    assert log["request_headers"][header] == REDACTED
    serialized = json.dumps(log)
    for secret in secrets:
        assert secret not in serialized


def test_sensitive_header_names_are_redacted(httpx_mock):
    operation = {"parameters": [{"name": "x-api-key", "in": "header", "required": True, "schema": {"type": "string"}}]}
    httpx_mock.add_response(url=URL, headers={"Set-Cookie": "session=s3cret", "Content-Type": "text/plain"})

    [log] = _run_tool(operation, {"headers": {"x-api-key": "llm-supplied"}})

    assert log["request_headers"]["x-api-key"] == REDACTED
    assert log["response_headers"]["set-cookie"] == REDACTED
    assert log["response_headers"]["content-type"] == "text/plain"


def test_long_bodies_are_truncated(httpx_mock):
    httpx_mock.add_response(url=URL, text="a" * (MAX_BODY_LENGTH + 5))

    [log] = _run_tool({})

    assert log["response_body"] == "a" * MAX_BODY_LENGTH + "\n... [truncated 5 characters]"


def test_error_response_is_logged(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=500, text="boom")

    [log] = _run_tool({})

    assert log["status_code"] == 500
    assert log["response_body"] == "boom"
    assert log["error"].startswith("HTTPStatusError: Server error '500 Internal Server Error'")


def test_transport_error_is_logged(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectTimeout("timed out"), url=URL)

    [log] = _run_tool({})

    assert log["url"] == URL
    assert log["status_code"] is None
    assert log["response_headers"] == {}
    assert log["response_body"] is None
    assert log["error"] == "ConnectTimeout: timed out"


def test_attachment_body_is_not_logged(httpx_mock):
    httpx_mock.add_response(
        url=URL, content=b"\x00\x01binary", headers={"Content-Disposition": "attachment; filename=report.pdf"}
    )

    [log] = _run_tool({})

    assert log["status_code"] == 200
    assert log["response_body"] == "[attachment not logged]"


def test_each_retry_is_logged(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=429, headers={"Retry-After": "0.01"})
    httpx_mock.add_response(url=URL, text="ok")

    logs = _run_tool({})

    assert [log["status_code"] for log in logs] == [429, 200]


def test_call_outside_a_tool_run_is_not_logged(httpx_mock):
    httpx_mock.add_response(url=URL, text="ok")
    spec = OpenAPISpec.from_spec_dict(make_openapi_schema({}))
    function_def = openapi_spec_op_to_function_def(spec, "/test", "get")
    executor = OpenAPIOperationExecutor(anonymous_auth_service, function_def)

    assert executor.call_api() == ("ok", None)


@pytest.mark.django_db()
def test_request_log_is_saved_on_the_trace(httpx_mock, experiment):
    httpx_mock.add_response(url=URL, text="ok")
    session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team)
    tracing_service = TracingService([OCSTracer(experiment, experiment.team_id)], experiment.id, experiment.team_id)
    tool = _build_tool({})

    with tracing_service.trace("chat", session):
        tool.invoke(
            {"name": tool.name, "args": {}, "id": "123", "type": "tool_call"},
            config=tracing_service.get_langchain_config(),
        )

    trace = Trace.objects.get(session=session)
    [log] = trace.custom_action_requests
    assert (log["method"], log["url"], log["status_code"], log["response_body"]) == ("GET", URL, 200, "ok")
