import pytest
from langchain_community.utilities.openapi import OpenAPISpec
from langchain_core.tools import Tool
from langchain_core.utils.function_calling import convert_to_openai_function

from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def


def test_openapi_spec_to_openai_function():
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "location",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "The location to get the weather for",
                }
            ],
        },
        path="/weather",
    )
    function_spec = _get_openai_function_from_openapi_spec(
        spec,
        "/weather",
        "get",
    )
    assert function_spec == _get_function_schema(
        "weather_get",
        "GET /weather endpoint",
        {
            "params": {
                "properties": {"location": {"description": "The location to get the weather for", "type": "string"}},
                "required": ["location"],
                "type": "object",
                "additionalProperties": False,
            }
        },
    )


def test_openai_function_with_optional_params():
    """Optional params in the OpenAPI schema become required in the OpenAI function."""
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "optional_param",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": "An optional parameter",
                }
            ],
        }
    )
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "get")
    assert function_spec == _get_function_schema(
        "test_get",
        "GET /test endpoint",
        {
            "params": {
                "properties": {"optional_param": {"description": "An optional parameter", "type": "string"}},
                "type": "object",
                "additionalProperties": False,
                "required": ["optional_param"],
            },
        },
    )


def test_openai_function_with_enum_params():
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "enum_param",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string", "enum": ["value1", "value2"]},
                    "description": "An enum parameter",
                }
            ],
        }
    )
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "get")
    assert function_spec == _get_function_schema(
        "test_get",
        "GET /test endpoint",
        {
            "params": {
                "properties": {
                    "enum_param": {
                        "description": "An enum parameter",
                        "type": "string",
                        "enum": ["value1", "value2"],
                    }
                },
                "required": ["enum_param"],
                "type": "object",
                "additionalProperties": False,
            },
        },
    )


def test_openai_function_with_path_params():
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "The ID of the resource",
                }
            ],
        },
        path="/test/{id}",
    )
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test/{id}", "get")
    assert function_spec == _get_function_schema(
        "test__id__get",
        "GET /test/{id} endpoint",
        {
            "path_params": {
                "properties": {"id": {"description": "The ID of the resource", "type": "string"}},
                "required": ["id"],
                "type": "object",
                "additionalProperties": False,
            }
        },
    )


def test_openai_function_with_multiple_methods():
    spec = _make_openapi_schema({"parameters": []})
    spec["paths"]["/test"]["post"] = {"summary": "Test POST endpoint"}
    get_function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "get")
    post_function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "post")

    assert get_function_spec == _get_function_schema("test_get", "GET /test endpoint", {})
    assert post_function_spec == _get_function_schema("test_post", "Test POST endpoint", {})


def test_openai_function_with_request_body():
    spec = _make_openapi_schema(
        {
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
                }
            },
        },
        method="post",
    )
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "post")
    assert function_spec == _get_function_schema(
        "test_post",
        "POST /test endpoint",
        {
            "body_data": {
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "type": "object",
            }
        },
    )


def test_openai_function_with_refs():
    spec = _make_openapi_schema(
        {
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Location"}},
                }
            }
        },
        method="post",
    )
    spec["components"] = {
        "schemas": {
            "Location": {
                "type": "object",
                "properties": {
                    "coordinates": {"$ref": "#/components/schemas/Coordinates"},
                },
            },
            "Coordinates": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
            },
        },
    }
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "post")
    assert function_spec == _get_function_schema(
        "test_post",
        "POST /test endpoint",
        {
            "body_data": {
                "additionalProperties": False,
                "properties": {
                    "coordinates": {
                        "additionalProperties": False,
                        "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"}},
                        "required": ["latitude", "longitude"],
                        "type": "object",
                    }
                },
                "required": ["coordinates"],
                "type": "object",
            }
        },
    )


def test_openapi_spec_with_multiple_query_params():
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "param1",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "First parameter",
                },
                {
                    "name": "param2",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer"},
                    "description": "Second parameter",
                },
            ],
        },
        path="/test",
    )
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "get")
    assert function_spec == _get_function_schema(
        "test_get",
        "GET /test endpoint",
        {
            "params": {
                "properties": {
                    "param1": {"description": "First parameter", "type": "string"},
                    "param2": {"description": "Second parameter", "type": "integer"},
                },
                "required": ["param1", "param2"],
                "type": "object",
                "additionalProperties": False,
            }
        },
    )


def test_openapi_spec_with_header_params():
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "X-Custom-Header",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "Custom header",
                }
            ],
        },
    )
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test", "get")
    assert function_spec == _get_function_schema(
        "test_get",
        "GET /test endpoint",
        {
            "headers": {
                "properties": {"X-Custom-Header": {"description": "Custom header", "type": "string"}},
                "required": ["X-Custom-Header"],
                "type": "object",
                "additionalProperties": False,
            }
        },
    )


def test_missing_path():
    spec = _make_openapi_schema({"parameters": []})
    with pytest.raises(ValueError, match="No path found for /missing"):
        _get_openai_function_from_openapi_spec(spec, "/missing", "get")


def test_missing_method():
    spec = _make_openapi_schema({"parameters": []})
    with pytest.raises(ValueError, match="No delete method found for /test"):
        _get_openai_function_from_openapi_spec(spec, "/test", "delete")


def test_openapi_spec_with_unsupported_request_body():
    spec = _make_openapi_schema(
        {
            "requestBody": {
                "content": {
                    "text/plain": {"schema": {"type": "string"}},
                }
            },
        },
        method="post",
    )
    with pytest.raises(ValueError, match="Only application/json request bodies are supported"):
        _get_openai_function_from_openapi_spec(spec, "/test", "post")


def test_openapi_spec_with_duplicate_parameter_names():
    spec = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "param",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "First parameter",
                },
                {
                    "name": "param",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer"},
                    "description": "Second parameter",
                },
            ],
        },
        path="/test/{param}",
    )
    function_spec = _get_openai_function_from_openapi_spec(spec, "/test/{param}", "get")
    assert function_spec == _get_function_schema(
        "test__param__get",
        "GET /test/{param} endpoint",
        {
            "params": {
                "properties": {"param": {"description": "Second parameter", "type": "integer"}},
                "required": ["param"],
                "type": "object",
                "additionalProperties": False,
            },
            "path_params": {
                "properties": {"param": {"description": "First parameter", "type": "string"}},
                "required": ["param"],
                "type": "object",
                "additionalProperties": False,
            },
        },
    )


def _get_function_schema(name, description, parameter_props):
    extra = {}
    if required_params := list(parameter_props):
        extra["required"] = required_params
    return {
        "name": name,
        "description": description,
        "parameters": {
            "properties": parameter_props,
            "type": "object",
            "additionalProperties": False,
            **extra,
        },
        "strict": True,
    }


def _get_openai_function_from_openapi_spec(spec: dict, path: str, method: str):
    """This does a round trip from OpenAPI spec to OpenAI function definition because it's hard
    to validate the pydantic model that's produced by `openapi_spec_op_to_function_def`.
    """
    spec = OpenAPISpec.from_spec_dict(spec)  # ty: ignore[invalid-assignment]
    function_def = openapi_spec_op_to_function_def(spec, path, method)  # ty: ignore[invalid-argument-type]
    tool = Tool(
        name=function_def.name,
        description=function_def.description,
        args_schema=function_def.args_schema,
        func=lambda x: x,
    )
    return convert_to_openai_function(tool, strict=True)


def _make_openapi_schema(properties, name="Test API", path="/test", method="get", openapi_version="3.0.0"):
    return {
        "openapi": openapi_version,
        "info": {"title": name, "version": "1.0.0"},
        "servers": [{"url": "https://example.com"}],
        "paths": {
            path: {
                method: {
                    "summary": f"{method.upper()} {path} endpoint",
                    **properties,
                }
            }
        },
    }
