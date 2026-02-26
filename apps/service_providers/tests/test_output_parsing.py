from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from langchain_classic.agents.output_parsers.tools import ToolAgentAction
from langchain_core.agents import AgentStep
from langchain_core.messages import AIMessage, FunctionMessage
from langchain_core.messages.block_translators.openai import _convert_annotation_to_v1

from apps.service_providers.llm_service.datamodels import LlmChatResponse
from apps.service_providers.llm_service.main import LlmService, OpenAILlmService
from apps.service_providers.llm_service.parsers import parse_output_for_anthropic
from apps.utils.factories.files import FileFactory


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


@pytest.mark.django_db()
class TestDefaultParser:
    def test_llm_output_parsing(self, team_with_users):
        session = Mock(team_id=team_with_users.id)

        llm_output = Mock(spec=AIMessage)
        llm_output.text = "Hello world"
        llm_output.content_blocks = [
            {"type": "text", "text": "Hello", "annotations": []},
            {"type": "text", "text": "world", "annotations": []},
        ]

        parser = LlmService()
        result: LlmChatResponse = parser._default_parser(llm_output, session)

        assert result.text == "Hello world"
        assert len(result.generated_files) == 0
        assert len(result.cited_files) == 0

    @pytest.mark.parametrize("expect_citations", [True, False])
    @patch("apps.service_providers.llm_service.main.get_openai_container_file_contents")
    @patch("apps.files.models.File.download_link")
    def test_llm_output_parsing_openai(
        self, download_link_mock, get_file_contents_mock, expect_citations, team_with_users
    ):
        session = Mock(team_id=team_with_users.id)

        # Local file that will be cited
        FileFactory(external_id="file-123", team=team_with_users)

        # Remote generated file content
        get_file_contents_mock.return_value = BytesIO(b"This is a generated file.")
        download_link_mock.return_value = "https://files.example.com/download/file-456"

        llm_output = Mock(spec=AIMessage)
        llm_output.text = "Hello world"
        annotations = [
            # Annotation stating that an uploaded file was cited
            {"file_id": "file-123", "type": "file_citation"},
            # Annotation stating that a container file (generated) was referenced
            {
                "file_id": "file-456",
                "type": "container_file_citation",
                "container_id": "container-1",
                "filename": "generated.txt",
            },
        ]
        llm_output.content_blocks = [
            {"type": "text", "text": "Hello", "annotations": []},
            {
                "type": "text",
                "text": "world",
                # Langchain does some standardization with annotations, hence we call _convert_annotation_to_v1
                # which does the same conversion as langchain's internal code.
                "annotations": [_convert_annotation_to_v1(an) for an in annotations],
            },
        ]

        parser = OpenAILlmService(openai_api_key="123")
        result: LlmChatResponse = parser._default_parser(llm_output, session, include_citations=expect_citations)

        assert result.text == "Hello world"
        assert len(result.generated_files) == 1
        assert result.generated_files.pop().file.read() == b"This is a generated file."

        if expect_citations:
            assert len(result.cited_files) == 1
            assert result.cited_files.pop().external_id == "file-123"
        else:
            assert len(result.cited_files) == 0

    def test_get_cited_file_ids_filters_none_values(self, team_with_users):
        """Test that None values are filtered out from citation file_ids.

        This handles cases where citations contain URLs instead of file IDs,
        which would result in None values when extracting file_id.
        """
        parser = OpenAILlmService(openai_api_key="123")

        # Annotation entries with mixed valid file_ids and None values (from URLs)
        annotation_entries = [
            {"type": "citation", "extras": {"file_id": "file-123"}},  # Valid file_id
            {"type": "citation", "extras": {"url": "https://example.com"}},  # URL instead of file_id (no file_id key)
            {"type": "citation", "extras": {"file_id": None}},  # Explicit None file_id
            {"type": "citation", "extras": {"file_id": "file-456"}},  # Valid file_id
            {"type": "citation", "extras": {}},  # Empty extras (no file_id key)
        ]

        result = parser.get_cited_file_ids(annotation_entries)

        # Should only return the valid file IDs, with None values filtered out
        assert result == ["file-123", "file-456"]
