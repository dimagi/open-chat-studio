from unittest.mock import Mock

import pytest
from langchain_community.utilities.openapi import OpenAPISpec
from langchain_core.messages import ToolMessage

from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def
from apps.chat.tests.test_openapi_tool import _make_openapi_schema
from apps.service_providers.auth_service import anonymous_auth_service


def test_openapi_tool_query_params(httpx_mock):
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "location",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "name": "type",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
        },
    )
    httpx_mock.add_response(url="https://example.com/test?location=Cape+Town&type=forecast")
    _test_tool_call(spec, {"params": {"location": "Cape Town", "type": "forecast"}})


def test_openapi_tool_path_params(httpx_mock):
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                },
            ],
        },
        path="/test/{id}",
    )
    httpx_mock.add_response(url="https://example.com/test/59")
    _test_tool_call(spec, {"path_params": {"id": "59"}})


def test_openapi_tool_body(httpx_mock):
    spec = _make_openapi_schema(
        {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "age": {"type": "integer"},
                            },
                        },
                    },
                },
            },
        },
    )
    httpx_mock.add_response(url="https://example.com/test", json={"name": "John", "age": 30})
    _test_tool_call(spec, {"body_data": {"name": "John", "age": 30}})


def test_openapi_tool_headers(httpx_mock):
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "x-custom-name",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
        },
    )
    httpx_mock.add_response(url="https://example.com/test", match_headers={"x-custom-name": "bob"})
    _test_tool_call(spec, {"headers": {"x-custom-name": "bob"}})


def test_openapi_tool_response_content_disposition_inline(httpx_mock):
    spec = _make_openapi_schema({"parameters": []})
    httpx_mock.add_response(
        url="https://example.com/test",
        content=b"content from API call",
        headers={"Content-Disposition": "inline"},
    )
    result = _test_tool_call(spec, {})
    assert result == ToolMessage(content="content from API call", name="test_get", tool_call_id="123")


@pytest.mark.parametrize("filename", ["", "; filename=example.txt"])
def test_openapi_tool_response_content_disposition_attachment(httpx_mock, filename):
    spec = _make_openapi_schema({"parameters": []})
    httpx_mock.add_response(
        url="https://example.com/test",
        content=b"content from API call",
        headers={"Content-Disposition": f"attachment{filename}", "Content-Type": "text/plain"},
    )
    result = _test_tool_call(spec, {})
    assert isinstance(result, ToolMessage)
    assert result.content == f"attachment{filename}"
    assert result.artifact.content == b"content from API call"
    assert result.artifact.content_type == "text/plain"
    if filename:
        assert result.artifact.name == "example.txt"


def _test_tool_call(spec_dict, call_args: dict, path=None):
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    path = path or list(spec.paths)[0]
    function_def = openapi_spec_op_to_function_def(spec, path, "get")
    tool = function_def.build_tool(auth_service=anonymous_auth_service, custom_action=Mock())
    return tool.run(call_args, tool_call_id="123")
