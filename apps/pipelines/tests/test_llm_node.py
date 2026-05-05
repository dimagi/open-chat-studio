from unittest.mock import Mock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.pipelines.exceptions import PipelineNodeRunError
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
