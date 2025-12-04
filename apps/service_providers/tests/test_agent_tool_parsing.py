import json
from pathlib import Path

import pytest
from langchain_classic.agents.output_parsers.tools import ToolAgentAction
from langchain_core.agents import AgentFinish
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, AIMessageChunk

from apps.service_providers.llm_service.parsers import custom_parse_ai_message


def load_test_data(filename: str) -> dict:
    """Load test data from the data directory."""
    data_path = Path(__file__).parent / "data" / filename
    with open(data_path) as f:
        return json.load(f)


class TestCustomParseAiMessage:
    """Test cases for the custom_parse_ai_message function."""

    def test_invalid_message_type(self):
        with pytest.raises(TypeError, match="Expected an AI message got"):
            custom_parse_ai_message("not an ai message")

    def test_message_without_tool_calls_returns_agent_finish(self):
        message = AIMessage(content="Hello, how can I help you?")
        result = custom_parse_ai_message(message)

        assert isinstance(result, AgentFinish)

    def test_anthropic_tool_call(self):
        """Test parsing Anthropic tool call message."""
        data = load_test_data("anthropic_tool_call.json")
        message = AIMessageChunk.model_validate(data)

        result = custom_parse_ai_message(message)

        assert isinstance(result, list)
        assert len(result) == 1

        action = result[0]
        assert isinstance(action, ToolAgentAction)
        assert action.tool == "one-off-reminder"
        assert action.tool_input == {
            "schedule_name": "Make Dinner Reminder",
            "message": "Make dinner",
            "datetime_due": "2025-05-29T16:04:38+02:00",
        }
        assert action.tool_call_id == "toolu_01WHbL5bh7AkAfYdDzgsEmKp"
        assert "one-off-reminder" in action.log
        assert "Make Dinner Reminder" in action.log

    def test_anthropic_web_search_tool(self):
        """Test that malformed Anthropic built-in tool calls are filtered out."""
        data = load_test_data("anthropic_web_search.json")
        message = AIMessageChunk.model_validate(data)

        result = custom_parse_ai_message(message)

        # Should return AgentFinish since the malformed tool call is filtered out
        assert isinstance(result, AgentFinish)
        # The content is a list of content blocks, so we need to check differently
        content_output = result.return_values["output"]
        assert isinstance(content_output, list)
        assert any(
            "Let me search for that." in str(block.get("text", ""))
            for block in content_output
            if isinstance(block, dict)
        )

    def test_openai_completions_api_tool_calls(self):
        """Test parsing OpenAI completions API tool calls (from both tool_calls and additional_kwargs)."""
        data = load_test_data("openai_completions_api_tool_calls.json")
        message = AIMessageChunk.model_validate(data)

        result = custom_parse_ai_message(message)

        assert isinstance(result, list)
        assert len(result) == 2

        tool_names = [action.tool for action in result]
        assert tool_names.count("update-user-data") == 1
        assert tool_names.count("one-off-reminder") == 1

    def test_openai_responses_api_tool_calls(self):
        """Test parsing OpenAI responses API tool calls."""
        data = load_test_data("openai_responses_api_tool_calls.json")
        message = AIMessageChunk.model_validate(data)

        result = custom_parse_ai_message(message)

        assert isinstance(result, list)
        assert len(result) == 1

        action = result[0]
        assert isinstance(action, ToolAgentAction)
        assert action.tool == "update-user-data"
        assert action.tool_input == {"key": "name", "value": "Bob"}
        assert action.tool_call_id == "call_CHAjPaPFZRPpl23ckUooa75z"

    def test_openai_web_search(self):
        """Test OpenAI web search response with no tool calls."""
        data = load_test_data("openai_web_search.json")
        message = AIMessageChunk.model_validate(data)

        result = custom_parse_ai_message(message)

        assert isinstance(result, AgentFinish)
        # The content is a list of content blocks
        content_output = result.return_values["output"]
        assert isinstance(content_output, list)
        # Check that content contains weather information about Cape Town
        content_text = str(content_output)
        assert "Cape Town" in content_text

    def test_additional_kwargs_tool_calls_invalid_json_raises_exception(self):
        """Test that invalid JSON in additional_kwargs tool calls raises OutputParserException."""
        message = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call_id",
                        "function": {
                            "name": "test_function",
                            "arguments": "{invalid json",  # Invalid JSON
                        },
                        "type": "function",
                    }
                ]
            },
        )

        with pytest.raises(OutputParserException, match="Could not parse tool input"):
            custom_parse_ai_message(message)

    def test_combined_tool_calls_and_additional_kwargs(self):
        """Test message with both tool_calls and additional_kwargs tool calls doesn't produce duplicates."""
        data = {
            "content": "Multiple tool calls.",
            "tool_calls": [{"name": "tool1", "args": {"param1": "value1"}, "id": "id1", "type": "tool_call"}],
            "additional_kwargs": {
                "tool_calls": [
                    {
                        "id": "id1",
                        "function": {"name": "tool1", "arguments": '{"param1": "value1"}'},
                        "type": "function",
                    }
                ]
            },
            "type": "ai",
        }
        message = AIMessage.model_validate(data)

        result = custom_parse_ai_message(message)

        assert isinstance(result, list)
        assert len(result) == 1

        action1 = result[0]
        assert action1.tool == "tool1"
        assert action1.tool_input == {"param1": "value1"}
        assert action1.tool_call_id == "id1"
