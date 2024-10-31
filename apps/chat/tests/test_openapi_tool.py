import re
from contextlib import nullcontext as does_not_raise

import pytest
import tenacity
from langchain_core.tools import ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool

from apps.chat.agent.openapi_tool import OpenAPITool
from apps.custom_actions.models import CustomAction
from apps.service_providers.auth_service import AuthService


class TestOpenAPITool:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Weather API", "version": "1.0.0"},
        "servers": [{"url": "https://api.weather.com"}],
        "paths": {
            "/weather": {
                "get": {
                    "summary": "Get weather",
                    "parameters": [
                        {
                            "name": "location",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "The location to get the weather for",
                        }
                    ],
                }
            }
        },
    }

    def _get_tool(self):
        action = CustomAction(
            name="Weather Service",
            description="Get the weather for a specific location",
            prompt="custom_instructions",
            api_schema=self.spec,
        )
        # raise exceptions in tests
        return OpenAPITool(custom_actions=[action], handle_tool_error=False)

    def test_spec(self):
        tool = self._get_tool()
        openai_tool = convert_to_openai_tool(tool, strict=True)
        assert len(openai_tool["function"]["description"]) < 1024, "Description must be less than 1024 characters"
        assert re.match(r"^[a-zA-Z0-9_-]{1,64}$", openai_tool["function"]["name"]), "Name must match regex"

    @pytest.mark.parametrize(
        ("tool_call", "url_params", "error_expectation"),
        [
            pytest.param(
                {"endpoint": "/weather", "method": "get"},
                "",
                None,
                id="no-params",
            ),
            pytest.param(
                {"endpoint": "/weather", "method": "get", "params": {"location": "Cape Town"}},
                "?location=Cape+Town",
                None,
                id="query-params",
            ),
            pytest.param(
                {"endpoint": "/other", "method": "get"},
                "",
                pytest.raises(ToolException),
                id="incorrect-endpoint",
            ),
            pytest.param(
                {"endpoint": "/weather", "method": "post"},
                "",
                pytest.raises(ToolException),
                id="incorrect-method",
            ),
        ],
    )
    def test_custom_action_execution(self, httpx_mock, tool_call: dict, url_params, error_expectation):
        tool = self._get_tool()

        if not error_expectation:
            expected_url = f"https://api.weather.com/weather{url_params}"
            httpx_mock.add_response(url=expected_url, json={"current": "sunny"})

        tool_call.setdefault("params", None)
        tool_call.setdefault("data", None)
        tool_call.setdefault("headers", None)
        with error_expectation or does_not_raise():
            assert tool.invoke(tool_call) == '{"current": "sunny"}'

    @pytest.mark.parametrize(
        ("responses", "error_expectation"),
        [
            pytest.param([{"json": {"current": "sunny"}}], does_not_raise(), id="normal"),
            pytest.param([{"status_code": 429}, {"json": {"current": "sunny"}}], does_not_raise(), id="retry"),
            pytest.param(
                [{"status_code": 429}, {"status_code": 429}, {"status_code": 429}],
                pytest.raises(ToolException),
                id="retry_failed",
            ),
            pytest.param([{"status_code": 500}], pytest.raises(ToolException), id="http_error"),
        ],
    )
    def test_http_errors(self, httpx_mock, responses, error_expectation):
        tool = self._get_tool()
        auth_service = AuthService()
        auth_service.__dict__["_default_retry_wait"] = lambda: tenacity.wait_none()
        tool.executors[0].auth_service = auth_service

        expected_url = "https://api.weather.com/weather?location=Cape+Town"
        for response in responses:
            httpx_mock.add_response(url=expected_url, **response)

        tool_call = {
            "endpoint": "/weather",
            "method": "get",
            "params": {"location": "Cape Town"},
            "data": None,
            "headers": None,
        }
        with error_expectation:
            assert tool.invoke(tool_call) == '{"current": "sunny"}'
