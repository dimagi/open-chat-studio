from langchain_community.utilities.openapi import OpenAPISpec

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


def _test_tool_call(spec_dict, call_args: dict, path=None):
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    path = path or list(spec.paths)[0]
    function_def = openapi_spec_op_to_function_def(spec, path, "get")
    tool = function_def.build_tool(auth_service=anonymous_auth_service)
    tool.run(call_args)
