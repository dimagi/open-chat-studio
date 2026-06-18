from unittest.mock import Mock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.pipelines.exceptions import PipelineNodeRunError
from apps.pipelines.nodes.helpers import get_system_message, prompt_uses_current_datetime
from apps.pipelines.nodes.history_middleware import MessageSizeValidationMiddleware
from apps.pipelines.nodes.llm_node import (
    _add_current_datetime_to_turn,
    _build_size_validation_middleware,
    build_node_agent,
)
from apps.pipelines.repository import RepositoryLookupError
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
        monkeypatch.setattr("apps.pipelines.nodes.llm_node._get_prompt_context", lambda *args, **kwargs: Mock())
        monkeypatch.setattr(
            "apps.pipelines.nodes.llm_node.get_system_message",
            lambda *args, **kwargs: SystemMessage(content="prompt"),
        )

        node = Mock()
        node.get_llm_service.return_value = service
        node.build_history_middleware.return_value = None
        build_node_agent(node, context=Mock(), session=Mock(), tool_callbacks=Mock())
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

    def test_system_message_coarsens_current_datetime_to_date(self):
        prompt_context = Mock()
        prompt_context.get_context.return_value = {"current_datetime": "Monday, 16 June 2026 14:32:05 UTC"}
        prompt_context.get_current_date.return_value = "Monday, 16 June 2026"

        message = get_system_message("Today is {current_datetime}", prompt_context)

        assert message.content == "Today is Monday, 16 June 2026"
        assert "14:32:05" not in message.content
        prompt_context.get_current_date.assert_called_once()

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


class TestMessageSizeValidationMiddleware:
    """Tests for MessageSizeValidationMiddleware and _build_size_validation_middleware."""

    def _make_node(self, max_token_limit: int):
        node = Mock()
        llm_model = Mock()
        llm_model.max_token_limit = max_token_limit
        node.repo.get_llm_provider_model.return_value = llm_model
        node.llm_provider_model_id = 1
        return node

    def _state(self, *messages):
        return {"messages": list(messages)}

    # --- MessageSizeValidationMiddleware.before_model ---

    def test_message_within_budget_passes(self):
        middleware = MessageSizeValidationMiddleware(token_limit=1000)
        middleware.before_model(self._state(HumanMessage(content="short message")), runtime=None)

    def test_message_exceeds_budget_raises(self):
        middleware = MessageSizeValidationMiddleware(token_limit=5)
        with pytest.raises(PipelineNodeRunError, match="too large"):
            middleware.before_model(
                self._state(HumanMessage(content="one two three four five six seven eight nine ten")),
                runtime=None,
            )

    def test_zero_token_limit_skips_validation(self):
        middleware = MessageSizeValidationMiddleware(token_limit=0)
        middleware.before_model(self._state(HumanMessage(content="word " * 10000)), runtime=None)

    # --- _build_size_validation_middleware ---

    def test_returns_none_when_no_token_limit(self):
        node = self._make_node(max_token_limit=0)
        result = _build_size_validation_middleware(node, SystemMessage(content=""))
        assert result is None

    @pytest.mark.parametrize(
        "exc",
        [RepositoryLookupError("not found"), RuntimeError("db timeout")],
    )
    def test_repo_error_propagates(self, exc):
        node = Mock()
        node.repo.get_llm_provider_model.side_effect = exc
        with pytest.raises(type(exc)):
            _build_size_validation_middleware(node, SystemMessage(content=""))

    def test_budget_accounts_for_system_message(self):
        # system=17 tokens → effective_limit=33; user message=42 tokens → raises
        node = self._make_node(max_token_limit=50)
        system_message = SystemMessage(content="word " * 10)
        middleware = _build_size_validation_middleware(node, system_message)
        assert middleware is not None
        with pytest.raises(PipelineNodeRunError, match="too large"):
            middleware.before_model(self._state(HumanMessage(content="word " * 30)), runtime=None)

    def test_always_uses_model_max_token_limit(self):
        node = self._make_node(max_token_limit=1000)
        middleware = _build_size_validation_middleware(node, SystemMessage(content=""))
        assert middleware is not None
        node.repo.get_llm_provider_model.assert_called_once()
