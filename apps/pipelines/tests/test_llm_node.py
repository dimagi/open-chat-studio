from unittest.mock import Mock

import httpx
import openai
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.chat.exceptions import UserReportableError
from apps.pipelines.nodes import llm_node
from apps.pipelines.nodes.helpers import get_system_message, prompt_uses_current_datetime
from apps.pipelines.nodes.llm_node import (
    _add_current_datetime_to_turn,
    build_node_agent,
)
from apps.service_providers.llm_service.image_types import DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES
from apps.service_providers.llm_service.main import AnthropicLlmService, OpenAILlmService


class TestBuildNodeAgentPromptCaching:
    """The node agent should include the LLM service's prompt caching middleware when one is provided."""

    def _build_agent_middleware(self, monkeypatch, service):
        captured = {}

        def fake_create_agent(**kwargs):
            captured.update(kwargs)
            return Mock()

        monkeypatch.setattr("apps.pipelines.nodes.llm_node.create_agent", fake_create_agent)
        monkeypatch.setattr("apps.pipelines.nodes.llm_node._get_configured_tools", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            "apps.pipelines.nodes.llm_node.get_system_message",
            lambda *args, **kwargs: SystemMessage(content="prompt"),
        )

        node = Mock()
        node.get_llm_service.return_value = service
        node.build_history_middleware.return_value = None
        build_node_agent(node, context=Mock(), session=Mock(), tool_callbacks=Mock(), prompt_context=Mock())
        return captured["middleware"]

    @pytest.mark.parametrize(
        ("service", "expected"),
        [
            pytest.param(
                AnthropicLlmService(anthropic_api_key="test", anthropic_api_base="https://api.anthropic.com"),
                True,
                id="anthropic_gets_caching_middleware",
            ),
            pytest.param(OpenAILlmService(openai_api_key="test"), False, id="openai_no_caching_middleware"),
        ],
    )
    def test_node_caching_middleware(self, monkeypatch, service, expected):
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

        middleware = self._build_agent_middleware(monkeypatch, service)
        assert any(isinstance(m, AnthropicPromptCachingMiddleware) for m in middleware) == expected


class TestCurrentDatetimeCachePreservation:
    """`{current_datetime}` must stay out of the cached system prompt prefix.

    It is coarsened to a day-precision date in the system prompt and the precise time is injected
    into the latest (uncached) message turn instead. See issue #3625.
    """

    @pytest.mark.parametrize(
        ("prompt", "expected"),
        [
            pytest.param("The time is {current_datetime}", True, id="used"),
            pytest.param("Hello {participant_data}", False, id="other_var"),
            pytest.param("No variables here", False, id="no_vars"),
        ],
    )
    def test_prompt_uses_current_datetime(self, prompt, expected):
        assert prompt_uses_current_datetime(prompt) is expected

    def test_system_message_requests_coarse_datetime(self):
        prompt_context = Mock()
        prompt_context.get_context.return_value = {"current_datetime": "Tuesday, 16 June 2026"}

        message = get_system_message("Today is {current_datetime}", prompt_context)

        assert message.content == "Today is Tuesday, 16 June 2026"
        # The context must render the coarse (day-precision) value, not the consumer fixing it up.
        _, kwargs = prompt_context.get_context.call_args
        assert kwargs["coarse_datetime"] is True

    def test_add_current_datetime_to_turn_injects_leading_block(self):
        node = Mock(prompt="Be useful {current_datetime}")
        prompt_context = Mock()
        prompt_context.get_current_datetime.return_value = "Monday, 16 June 2026 14:32:05 UTC"
        message = HumanMessage(content=[{"type": "text", "text": "hi"}])

        _add_current_datetime_to_turn(node, prompt_context, message)

        assert message.content == [
            {"type": "text", "text": "<current_datetime>Monday, 16 June 2026 14:32:05 UTC</current_datetime>"},
            {"type": "text", "text": "hi"},
        ]

    def test_add_current_datetime_to_turn_noop_when_not_used(self):
        node = Mock(prompt="Be useful {participant_data}")
        prompt_context = Mock()
        message = HumanMessage(content=[{"type": "text", "text": "hi"}])

        _add_current_datetime_to_turn(node, prompt_context, message)

        assert message.content == [{"type": "text", "text": "hi"}]
        prompt_context.get_current_datetime.assert_not_called()


class TestExecuteSubAgentImageErrorWiring:
    def test_provider_invalid_image_error_surfaces_as_user_reportable(self, monkeypatch):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        error = openai.BadRequestError(
            "bad image", response=httpx.Response(400, request=request), body={"code": "invalid_image_format"}
        )
        agent = Mock()
        agent.invoke.side_effect = error
        monkeypatch.setattr(llm_node, "build_node_agent", Mock(return_value=agent))
        monkeypatch.setattr(llm_node, "_get_prompt_context", Mock())
        monkeypatch.setattr(llm_node, "_add_current_datetime_to_turn", Mock())

        node = Mock()
        node.get_llm_service.return_value.supported_image_content_types = DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES
        context = Mock()
        context.input = "hello"
        context.attachments = []
        context.input_message_id = None
        context.state.participant_data = {}
        context.state.session_state = {}

        with pytest.raises(UserReportableError):
            llm_node.execute_sub_agent(node, context)
