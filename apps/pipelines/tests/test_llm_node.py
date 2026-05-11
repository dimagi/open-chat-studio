import math
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.pipelines.exceptions import MessageTooLargeError, PipelineNodeRunError
from apps.pipelines.nodes.history_middleware import MessageSizeValidationMiddleware
from apps.pipelines.nodes.llm_node import _build_size_validation_middleware
from apps.pipelines.repository import RepositoryLookupError


class TestMessageSizeValidationMiddleware:
    """Tests for MessageSizeValidationMiddleware and _build_size_validation_middleware."""

    def _make_model(self):
        """Mock model whose token counter uses ceil(chars / 4) — mirrors LangChain's approximation."""
        model = Mock()
        model.get_num_tokens_from_messages.side_effect = lambda msgs: math.ceil(
            sum(len(str(m.content)) for m in msgs) / 4
        )
        return model

    def _make_node(self, max_token_limit: int):
        node = Mock()
        llm_model = Mock()
        llm_model.max_token_limit = max_token_limit
        node.repo.get_llm_provider_model.return_value = llm_model
        node.llm_provider_model_id = 1
        node.get_chat_model.return_value = self._make_model()
        return node

    def _state(self, *messages):
        return {"messages": list(messages)}

    # --- MessageSizeValidationMiddleware.before_model ---

    def test_message_within_budget_passes(self):
        middleware = MessageSizeValidationMiddleware(token_limit=1000, model=self._make_model())
        middleware.before_model(self._state(HumanMessage(content="short message")), runtime=None)

    def test_message_exceeds_budget_raises(self):
        middleware = MessageSizeValidationMiddleware(token_limit=5, model=self._make_model())
        with pytest.raises(PipelineNodeRunError, match="too large"):
            middleware.before_model(
                self._state(HumanMessage(content="one two three four five six seven eight nine ten")),
                runtime=None,
            )

    def test_zero_token_limit_rejects_any_message(self):
        # token_limit=0 means the system prompt consumed the entire budget; any user message should be rejected.
        middleware = MessageSizeValidationMiddleware(token_limit=0, model=self._make_model())
        with pytest.raises(MessageTooLargeError):
            middleware.before_model(self._state(HumanMessage(content="hi")), runtime=None)

    def test_none_token_limit_skips_validation(self):
        middleware = MessageSizeValidationMiddleware(token_limit=None, model=self._make_model())
        middleware.before_model(self._state(HumanMessage(content="word " * 10000)), runtime=None)

    def test_large_tool_message_does_not_raise(self):
        # Regression: tool outputs inflated count and blocked legitimate conversations.
        # Only the last HumanMessage should be checked, not ToolMessages.
        middleware = MessageSizeValidationMiddleware(token_limit=50, model=self._make_model())
        state = self._state(
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"id": "t1", "name": "search", "args": {}}]),
            ToolMessage(content="word " * 5000, tool_call_id="t1"),
        )
        middleware.before_model(state, runtime=None)

    def test_no_human_message_skips_validation(self):
        # Guard: if the state has no HumanMessage (shouldn't happen normally) don't raise.
        middleware = MessageSizeValidationMiddleware(token_limit=5, model=self._make_model())
        state = self._state(SystemMessage(content="system prompt"))
        middleware.before_model(state, runtime=None)

    def test_only_last_human_message_is_checked(self):
        # History messages don't count — only the latest user input.
        middleware = MessageSizeValidationMiddleware(token_limit=20, model=self._make_model())
        state = self._state(
            HumanMessage(content="word " * 100),  # old, large message — must not trigger
            AIMessage(content="reply"),
            HumanMessage(content="hi"),  # current, small message
        )
        middleware.before_model(state, runtime=None)

    # --- _build_size_validation_middleware ---

    def test_returns_none_when_no_token_limit(self):
        node = self._make_node(max_token_limit=0)
        result = _build_size_validation_middleware(node, SystemMessage(content=""), self._make_model())
        assert result is None

    @pytest.mark.parametrize(
        "exc",
        [RepositoryLookupError("not found"), RuntimeError("db timeout")],
    )
    def test_repo_error_propagates(self, exc):
        node = Mock()
        node.repo.get_llm_provider_model.side_effect = exc
        with pytest.raises(type(exc)):
            _build_size_validation_middleware(node, SystemMessage(content=""), Mock())

    def test_budget_accounts_for_system_message(self):
        # system "word " * 10 = 50 chars → 13 tokens → effective_limit=37
        # user "word " * 30 = 150 chars → 38 tokens → raises
        node = self._make_node(max_token_limit=50)
        model = self._make_model()
        system_message = SystemMessage(content="word " * 10)
        middleware = _build_size_validation_middleware(node, system_message, model)
        assert middleware is not None
        with pytest.raises(PipelineNodeRunError, match="too large"):
            middleware.before_model(self._state(HumanMessage(content="word " * 30)), runtime=None)

    def test_always_uses_model_max_token_limit(self):
        node = self._make_node(max_token_limit=1000)
        middleware = _build_size_validation_middleware(node, SystemMessage(content=""), self._make_model())
        assert middleware is not None
        node.repo.get_llm_provider_model.assert_called_once()
