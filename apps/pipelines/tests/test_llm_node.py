from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.pipelines.exceptions import MessageTooLargeError, PipelineNodeRunError
from apps.pipelines.nodes.history_middleware import MessageSizeValidationMiddleware
from apps.pipelines.nodes.llm_node import _build_size_validation_middleware
from apps.pipelines.repository import RepositoryLookupError


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

    def test_zero_token_limit_rejects_any_message(self):
        # token_limit=0 means the system prompt consumed the entire budget; any user message should be rejected.
        middleware = MessageSizeValidationMiddleware(token_limit=0)
        with pytest.raises(MessageTooLargeError):
            middleware.before_model(self._state(HumanMessage(content="hi")), runtime=None)

    def test_none_token_limit_skips_validation(self):
        middleware = MessageSizeValidationMiddleware(token_limit=None)
        middleware.before_model(self._state(HumanMessage(content="word " * 10000)), runtime=None)

    def test_large_tool_message_does_not_raise(self):
        # Regression: tool outputs inflated count and blocked legitimate conversations.
        # Only the last HumanMessage should be checked, not ToolMessages.
        middleware = MessageSizeValidationMiddleware(token_limit=50)
        state = self._state(
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"id": "t1", "name": "search", "args": {}}]),
            ToolMessage(content="word " * 5000, tool_call_id="t1"),
        )
        middleware.before_model(state, runtime=None)

    def test_no_human_message_skips_validation(self):
        # Guard: if the state has no HumanMessage (shouldn't happen normally) don't raise.
        middleware = MessageSizeValidationMiddleware(token_limit=5)
        state = self._state(SystemMessage(content="system prompt"))
        middleware.before_model(state, runtime=None)

    def test_only_last_human_message_is_checked(self):
        # History messages don't count — only the latest user input.
        middleware = MessageSizeValidationMiddleware(token_limit=20)
        state = self._state(
            HumanMessage(content="word " * 100),  # old, large message — must not trigger
            AIMessage(content="reply"),
            HumanMessage(content="hi"),  # current, small message
        )
        middleware.before_model(state, runtime=None)

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
