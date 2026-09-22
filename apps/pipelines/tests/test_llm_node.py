import logging
from unittest.mock import Mock

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.chat.exceptions import (
    EmptyModelResponseError,
    ModelRefusedTurnError,
    ProviderConfigurationError,
    UserActionableError,
)
from apps.pipelines.nodes import llm_node
from apps.pipelines.nodes.helpers import get_system_message, prompt_uses_current_datetime
from apps.pipelines.nodes.llm_node import (
    _add_current_datetime_to_turn,
    _get_final_ai_message,
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
    def test_provider_invalid_image_error_surfaces_as_user_actionable(self, monkeypatch):
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

        with pytest.raises(UserActionableError):
            llm_node.execute_sub_agent(node, context)


def _history(content: str, message_id: int) -> AIMessage:
    """A replayed history message, which carries its DB id the way ChatMessage.to_langchain_dict does."""
    return AIMessage(content=content, additional_kwargs={"id": message_id})


def _tool_turn() -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1", "type": "tool_call"}])


class TestGetFinalAiMessage:
    def test_walks_back_over_a_tool_only_trailing_turn(self):
        answer = AIMessage(content="Here is the answer")
        messages = [HumanMessage(content="q"), answer, _tool_turn(), ToolMessage(content="ok", tool_call_id="c1")]

        assert _get_final_ai_message(messages) is answer

    def test_stops_at_a_refused_turn(self):
        answer = AIMessage(content="Earlier answer")
        refused = AIMessage(content="", response_metadata={"stop_reason": "refusal"})

        assert _get_final_ai_message([HumanMessage(content="q"), answer, refused]) is refused

    def test_stops_at_a_filtered_turn(self):
        filtered = AIMessage(content="", response_metadata={"finish_reason": "content_filter"})

        assert _get_final_ai_message([AIMessage(content="Earlier"), filtered]) is filtered

    def test_does_not_walk_into_replayed_history(self):
        empty = AIMessage(content="")
        messages = [_history("old answer", 1), HumanMessage(content="new q"), empty]

        assert _get_final_ai_message(messages) is empty

    def test_returns_an_empty_message_when_this_turn_has_no_ai_message(self):
        messages = [_history("old answer", 1), HumanMessage(content="q"), ToolMessage(content="x", tool_call_id="c")]

        result = _get_final_ai_message(messages)

        assert isinstance(result, AIMessage)
        assert result.text == ""


def _node_and_context(final_messages):
    agent = Mock()
    agent.invoke.return_value = {"messages": final_messages}
    node = Mock()
    node.name = "Answer"
    node.node_id = "node-1"
    node.get_llm_service.return_value.supported_image_content_types = DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES
    context = Mock()
    context.input = "hello"
    context.attachments = []
    context.input_message_id = None
    context.state.participant_data = {}
    context.state.session_state = {}
    return agent, node, context


class TestExecuteSubAgentOutcomes:
    @pytest.fixture(autouse=True)
    def _wire(self, monkeypatch):
        monkeypatch.setattr(llm_node, "_get_prompt_context", Mock())
        monkeypatch.setattr(llm_node, "_add_current_datetime_to_turn", Mock())

    def _run(self, monkeypatch, final_messages):
        agent, node, context = _node_and_context(final_messages)
        monkeypatch.setattr(llm_node, "build_node_agent", Mock(return_value=agent))
        return node, lambda: llm_node.execute_sub_agent(node, context)

    def test_refused_turn_raises_before_history_is_saved(self, monkeypatch):
        node, run = self._run(monkeypatch, [AIMessage(content="", response_metadata={"stop_reason": "refusal"})])

        with pytest.raises(ModelRefusedTurnError) as exc_info:
            run()

        assert exc_info.value.kind == "refusal"
        node.save_history.assert_not_called()

    def test_filtered_turn_raises(self, monkeypatch):
        _, run = self._run(monkeypatch, [AIMessage(content="", response_metadata={"finish_reason": "SAFETY"})])

        with pytest.raises(ModelRefusedTurnError) as exc_info:
            run()

        assert exc_info.value.kind == "content_filter"
        assert exc_info.value.provider_reason == "SAFETY"

    def test_length_stop_without_text_is_team_actionable(self, monkeypatch):
        _, run = self._run(monkeypatch, [AIMessage(content="", response_metadata={"finish_reason": "length"})])

        with pytest.raises(ProviderConfigurationError, match="Node: Answer"):
            run()

    def test_empty_turn_is_a_plain_fault(self, monkeypatch):
        _, run = self._run(monkeypatch, [_history("old", 1), HumanMessage(content="q"), AIMessage(content="")])

        with pytest.raises(EmptyModelResponseError):
            run()

    def test_logs_the_kind_and_stop_reason_only(self, monkeypatch, caplog):
        message = AIMessage(content="secret text", response_metadata={"stop_reason": "refusal"})
        _, run = self._run(monkeypatch, [message])

        with caplog.at_level(logging.INFO, logger="ocs.pipelines.nodes"), pytest.raises(ModelRefusedTurnError):
            run()

        assert "refusal" in caplog.text
        assert "secret text" not in caplog.text

    def test_truncated_answer_is_delivered_and_logged(self, monkeypatch, caplog):
        message = AIMessage(content="Cut off", response_metadata={"finish_reason": "length"})
        node, run = self._run(monkeypatch, [message])
        monkeypatch.setattr(llm_node, "_process_agent_output", Mock(return_value=("Cut off", {})))

        with caplog.at_level(logging.INFO, logger="ocs.pipelines.nodes"):
            run()

        node.save_history.assert_called_once()
        assert "length" in caplog.text

    def test_answered_turn_is_delivered_without_logging(self, monkeypatch, caplog):
        node, run = self._run(monkeypatch, [AIMessage(content="All good")])
        monkeypatch.setattr(llm_node, "_process_agent_output", Mock(return_value=("All good", {})))

        with caplog.at_level(logging.INFO, logger="ocs.pipelines.nodes"):
            run()

        node.save_history.assert_called_once()
        assert caplog.text == ""
