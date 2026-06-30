from unittest.mock import Mock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.pipelines.nodes.helpers import get_system_message, prompt_uses_current_datetime
from apps.pipelines.nodes.llm_node import (
    _add_current_datetime_to_turn,
    build_node_agent,
)
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
