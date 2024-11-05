from langchain_community.utilities.openapi import OpenAPISpec

from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def
from apps.chat.tests.test_openapi_tool import _make_openapi_schema
from apps.service_providers.auth_service import anonymous_auth_service


def test_openapi_spec_to_openai_function(httpx_mock):
    spec_dict = _make_openapi_schema(
        {
            "parameters": [
                {
                    "name": "location",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
        },
        path="/weather",
    )
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    function_def = openapi_spec_op_to_function_def(spec, "/weather", "get")
    tool = function_def.build_tool(auth_service=anonymous_auth_service)

    httpx_mock.add_response(url="https://example.com/weather?location=Cape+Town")

    tool.run({"params": {"location": "Cape Town"}})
