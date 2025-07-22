from langchain.agents.output_parsers.tools import ToolAgentAction
from langchain_core.agents import AgentStep
from langchain_core.messages import AIMessage, FunctionMessage

from apps.service_providers.llm_service.datamodels import LlmChatResponse
from apps.service_providers.llm_service.parsers import parse_output_for_anthropic


class TestParseOutputForAnthropic:
    def test_none_input(self):
        assert parse_output_for_anthropic(None, session=None) == LlmChatResponse(text="")

    def test_empty_string_input(self):
        assert parse_output_for_anthropic("", session=None) == LlmChatResponse(text="")

    def test_string_input(self):
        text = "Hello, world!"
        assert parse_output_for_anthropic(text, session=None) == LlmChatResponse(text=text)

    def test_dict_with_output_key(self):
        output = {"output": "This is the response"}
        assert parse_output_for_anthropic(output, session=None) == LlmChatResponse(text="This is the response")

    def test_dict_with_text_key(self):
        output = {"type": "text", "text": "This is the text content"}
        assert parse_output_for_anthropic(output, session=None) == LlmChatResponse(text="This is the text content")

    def test_dict_without_output_or_text_key(self):
        output = {"some_key": "some_value"}
        assert parse_output_for_anthropic(output, session=None) == LlmChatResponse(text="")

    def test_list_with_text_objects(self):
        output = [{"text": "Hello", "type": "text", "index": 0}, {"text": " world!", "type": "text", "index": 1}]
        assert parse_output_for_anthropic(output, session=None) == LlmChatResponse(text="Hello world!")

    def test_list_with_text_objects_and_citations(self):
        output = [
            {
                "text": "Here's some information",
                "type": "text",
                "index": 0,
                "citations": [
                    {
                        "cited_text": "The weather forecast",
                        "encrypted_index": "123123",
                        "title": "Weather",
                        "type": "web_search_result_location",
                        "url": "https://weather.com/",
                    },
                    {"title": "Source 2", "url": "https://example.com/2"},
                ],
            }
        ]
        expected = LlmChatResponse(
            text="Here's some information [Weather](https://weather.com/) [Source 2](https://example.com/2)"
        )
        assert parse_output_for_anthropic(output, session=None) == expected

    def test_list_with_mixed_content(self):
        output = [
            {"text": "Text part", "type": "text", "index": 0},
            "String part",
            {"type": "other", "content": "should be ignored"},
        ]
        assert parse_output_for_anthropic(output, session=None) == LlmChatResponse(text="Text partString part")

    def test_list_with_tool_use_objects_are_ignored(self):
        output = [
            {"text": "I'll help you", "type": "text", "index": 0},
            {"id": "tool_123", "name": "update-user-data", "type": "tool_use", "input": {}},
        ]
        assert parse_output_for_anthropic(output, session=None) == LlmChatResponse(text="I'll help you")

    def test_sample_output_with_actions_dict(self):
        result = parse_output_for_anthropic(
            {
                "actions": [
                    {"type": "tool_use", "name": "update-user-data", "input": {"key": "name", "value": "Jack"}},
                ]
            },
            session=None,
        )
        assert result == LlmChatResponse(text="")

    def test_sample_output_with_steps_dict(self):
        result = parse_output_for_anthropic(
            {
                "steps": [
                    AgentStep(
                        action=ToolAgentAction(
                            tool="update-user-data",
                            tool_input={"key": "name", "value": "Jack"},
                            log="",
                            message_log=[],
                            tool_call_id="toolu_01PKKDDt5tsNPvXCjHSgZ8bN",
                        ),
                        observation="Success",
                    )
                ],
                "messages": [
                    FunctionMessage(
                        content="Success", additional_kwargs={}, response_metadata={}, name="update-user-data"
                    )
                ],
            },
            session=None,
        )
        assert result == LlmChatResponse(text="")

    def test_sample_output_with_proper_output_dict(self):
        result = parse_output_for_anthropic(
            {
                "output": [
                    {
                        "text": "Is there anything else you need help with?",
                        "type": "text",
                        "index": 0,
                    }
                ],
                "messages": [
                    AIMessage(
                        content=[{"text": "Is there anything else you need help with?", "type": "text", "index": 0}],
                        additional_kwargs={},
                        response_metadata={},
                    )
                ],
            },
            session=None,
        )
        expected = LlmChatResponse(text="Is there anything else you need help with?")
        assert result == expected

    def test_empty_list(self):
        assert parse_output_for_anthropic([], session=None) == LlmChatResponse(text="")

    def test_list_with_no_valid_items(self):
        output = [
            {"type": "tool_use", "name": "some_tool"},
            {"type": "other", "data": "something"},
            123,  # Non-dict, non-string item
            None,  # None item
        ]
        assert parse_output_for_anthropic(output, session=None) == LlmChatResponse(text="")

    def test_citations_without_title_or_url(self):
        output = [
            {
                "text": "Text with incomplete citations",
                "type": "text",
                "citations": [
                    {"title": "Title only"},  # Missing URL
                    {"url": "https://example.com"},  # Missing title
                    {"title": "Complete", "url": "https://example.com/complete"},  # Complete
                ],
            }
        ]
        expected = LlmChatResponse(text="Text with incomplete citations [Complete](https://example.com/complete)")
        assert parse_output_for_anthropic(output, session=None) == expected
