import json
from contextlib import nullcontext as does_not_raise

import pytest
import tenacity
from langchain_core.tools import ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool

from apps.chat.agent.tools import get_custom_action_tools
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
        [tool] = get_custom_action_tools([action])
        tool.handle_tool_error = False  # raise exceptions in tests
        return tool

    def test_tool_function_schema(self):
        tool = self._get_tool()
        tool_spec = convert_to_openai_tool(tool, strict=True)
        assert "weather-api" in tool_spec["function"]["name"]
        assert "custom_instructions" in tool_spec["function"]["description"]
        assert json.dumps(self.spec) in tool_spec["function"]["description"]

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
        tool.auth_service = auth_service

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
