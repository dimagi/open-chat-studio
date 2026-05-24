import math
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.pipelines.exceptions import MessageTooLargeError, PipelineNodeRunError
from apps.pipelines.nodes.history_middleware import MessageSizeValidationMiddleware


class TestMessageSizeValidationMiddleware:
    """Tests for MessageSizeValidationMiddleware."""

    def _make_model(self):
        """Mock model whose token counter uses ceil(chars / 4) — mirrors LangChain's approximation."""
        model = Mock()
        model.get_num_tokens_from_messages.side_effect = lambda msgs: math.ceil(
            sum(len(str(m.content)) for m in msgs) / 4
        )
        return model

    def _state(self, *messages):
        return {"messages": list(messages)}

    # --- MessageSizeValidationMiddleware.before_model ---

    def test_message_within_budget_passes(self):
        middleware = MessageSizeValidationMiddleware(
            token_limit=1000, token_counter=self._make_model().get_num_tokens_from_messages
        )
        middleware.before_model(self._state(HumanMessage(content="short message")), runtime=None)

    def test_message_exceeds_budget_raises(self):
        middleware = MessageSizeValidationMiddleware(
            token_limit=5, token_counter=self._make_model().get_num_tokens_from_messages
        )
        with pytest.raises(PipelineNodeRunError, match="too large"):
            middleware.before_model(
                self._state(HumanMessage(content="one two three four five six seven eight nine ten")),
                runtime=None,
            )

    def test_zero_token_limit_rejects_any_message(self):
        # token_limit=0 means the system prompt consumed the entire budget; any user message should be rejected.
        middleware = MessageSizeValidationMiddleware(
            token_limit=0, token_counter=self._make_model().get_num_tokens_from_messages
        )
        with pytest.raises(MessageTooLargeError):
            middleware.before_model(self._state(HumanMessage(content="hi")), runtime=None)

    def test_none_token_limit_skips_validation(self):
        middleware = MessageSizeValidationMiddleware(
            token_limit=None, token_counter=self._make_model().get_num_tokens_from_messages
        )
        middleware.before_model(self._state(HumanMessage(content="word " * 10000)), runtime=None)

    def test_large_tool_message_does_not_raise(self):
        # Regression (#3303): a large ToolMessage must not cause validation to raise.
        # token_limit=50 is far below the ToolMessage size (~6250 tokens) but both checks
        # pass because per-message "hi" ≈ 1 token and total excludes the ToolMessage.
        middleware = MessageSizeValidationMiddleware(
            token_limit=50, token_counter=self._make_model().get_num_tokens_from_messages
        )
        state = self._state(
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"id": "t1", "name": "search", "args": {}}]),
            ToolMessage(content="word " * 5000, tool_call_id="t1"),
        )
        middleware.before_model(state, runtime=None)

    def test_no_human_message_skips_validation(self):
        # Guard: if the state has no HumanMessage (shouldn't happen normally) don't raise.
        middleware = MessageSizeValidationMiddleware(
            token_limit=5, token_counter=self._make_model().get_num_tokens_from_messages
        )
        state = self._state(SystemMessage(content="system prompt"))
        middleware.before_model(state, runtime=None)

    def test_only_last_human_message_is_checked_for_per_message_limit(self):
        # Per-message check only validates the last HumanMessage, not earlier ones.
        # token_limit=200 is above the total conversation (~127 tokens) so the total
        # check also passes — this isolates the per-message behaviour.
        middleware = MessageSizeValidationMiddleware(
            token_limit=200, token_counter=self._make_model().get_num_tokens_from_messages
        )
        state = self._state(
            HumanMessage(content="word " * 100),  # old, large message — not validated per-message
            AIMessage(content="reply"),
            HumanMessage(content="hi"),  # current, small message
        )
        middleware.before_model(state, runtime=None)

    def test_total_context_raises_for_oversized_history(self):
        # Total-context check fires when accumulated conversation history (HumanMessages +
        # AIMessages) exceeds the budget, even when the most recent user message is small.
        # token_limit=50; total ≈ ceil((250+2+2)/4) = 64 tokens → raises.
        middleware = MessageSizeValidationMiddleware(
            token_limit=50, token_counter=self._make_model().get_num_tokens_from_messages
        )
        state = self._state(
            HumanMessage(content="word " * 50),  # 250 chars ≈ 63 tokens
            AIMessage(content="ok"),
            HumanMessage(content="hi"),
        )
        with pytest.raises(PipelineNodeRunError, match="history is too large"):
            middleware.before_model(state, runtime=None)

    def test_total_context_check_excludes_tool_and_system_messages(self):
        # ToolMessages and SystemMessages are excluded from the total context count.
        # A tight budget (50 tokens) that the ToolMessage alone would blow passes here
        # because only HumanMessage + AIMessage (~1 token) are counted.
        middleware = MessageSizeValidationMiddleware(
            token_limit=50, token_counter=self._make_model().get_num_tokens_from_messages
        )
        state = self._state(
            SystemMessage(content="word " * 100),  # excluded from total count
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"id": "t1", "name": "search", "args": {}}]),
            ToolMessage(content="word " * 5000, tool_call_id="t1"),  # excluded from total count
        )
        middleware.before_model(state, runtime=None)

    def test_budget_accounts_for_system_message(self):
        # system "word " * 10 = 50 chars → 13 tokens (ceil(50/4)) → effective_limit = 50 - 13 = 37
        # user "word " * 30 = 150 chars → 38 tokens → exceeds budget → raises
        model = self._make_model()
        max_token_limit = 50
        system_tokens = math.ceil(len("word " * 10) / 4)
        effective_limit = max(max_token_limit - system_tokens, 0)
        middleware = MessageSizeValidationMiddleware(
            token_limit=effective_limit, token_counter=model.get_num_tokens_from_messages
        )
        with pytest.raises(PipelineNodeRunError, match="too large"):
            middleware.before_model(self._state(HumanMessage(content="word " * 30)), runtime=None)
