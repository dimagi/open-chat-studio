import pytest
from langchain_core.messages import AIMessage

from apps.chat.exceptions import EmptyModelResponseError, ModelRefusedTurnError, ProviderConfigurationError
from apps.service_providers.llm_service.outcomes import (
    TurnOutcome,
    classify_turn,
    provider_reason,
    raise_for_outcome,
    refusal_text,
)


def _tool_call_message():
    return AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "call_1", "type": "tool_call"}])


CASES = [
    pytest.param(AIMessage(content="Hello"), "answered", "", id="plain_text"),
    pytest.param(
        AIMessage(content="Hello", response_metadata={"stop_reason": "end_turn", "stop_sequence": None}),
        "answered",
        "end_turn",
        id="anthropic_end_turn",
    ),
    pytest.param(_tool_call_message(), "answered", "", id="tool_call_turn"),
    pytest.param(
        AIMessage(
            content="",
            additional_kwargs={"refusal": "I cannot help with that."},
            response_metadata={"finish_reason": "stop"},
        ),
        "refusal",
        "refusal",
        id="openai_chat_completions_refusal",
    ),
    pytest.param(
        AIMessage(content="Hi", additional_kwargs={"refusal": None}, response_metadata={"finish_reason": "stop"}),
        "answered",
        "stop",
        id="openai_refusal_key_present_but_none",
    ),
    pytest.param(
        AIMessage(
            content=[{"type": "refusal", "refusal": "I cannot help with that.", "id": "msg_1"}],
            response_metadata={"status": "completed", "model_provider": "openai"},
        ),
        "refusal",
        "refusal",
        id="openai_responses_refusal_block",
    ),
    pytest.param(
        AIMessage(content="Partial text", response_metadata={"stop_reason": "refusal", "stop_details": None}),
        "refusal",
        "refusal",
        id="anthropic_refusal_with_partial_text",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"finish_reason": "LANGUAGE"}),
        "refusal",
        "LANGUAGE",
        id="gemini_language",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"finish_reason": "content_filter"}),
        "content_filter",
        "content_filter",
        id="openai_chat_completions_filter",
    ),
    pytest.param(
        AIMessage(content="Partial", response_metadata={"finish_reason": "content_filter"}),
        "content_filter",
        "content_filter",
        id="azure_filter_with_partial_text",
    ),
    pytest.param(
        AIMessage(
            content=[],
            response_metadata={"status": "incomplete", "incomplete_details": {"reason": "content_filter"}},
        ),
        "content_filter",
        "content_filter",
        id="openai_responses_filter",
    ),
    pytest.param(
        AIMessage(
            content="",
            response_metadata={
                "prompt_feedback": {"block_reason": 0, "safety_ratings": []},
                "finish_reason": "SAFETY",
                "safety_ratings": [{"category": "HARM_CATEGORY_HATE_SPEECH", "probability": "HIGH", "blocked": True}],
            },
        ),
        "content_filter",
        "SAFETY",
        id="gemini_candidate_block",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"is_blocked": True, "finish_reason": "SAFETY"}),
        "content_filter",
        "SAFETY",
        id="vertex_candidate_block",
    ),
    pytest.param(
        AIMessage(
            content="",
            response_metadata={"prompt_feedback": {"block_reason": 1, "safety_ratings": [{"category": 9}]}},
        ),
        "content_filter",
        "1",
        id="gemini_prompt_block",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"finish_reason": "length"}),
        "length",
        "length",
        id="openai_length",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"stop_reason": "max_tokens"}),
        "length",
        "max_tokens",
        id="anthropic_max_tokens",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"stop_reason": "model_context_window_exceeded"}),
        "length",
        "model_context_window_exceeded",
        id="anthropic_context_window",
    ),
    pytest.param(
        AIMessage(
            content=[],
            response_metadata={"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        ),
        "length",
        "max_output_tokens",
        id="openai_responses_max_output_tokens",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"finish_reason": "MAX_TOKENS"}),
        "length",
        "MAX_TOKENS",
        id="gemini_max_tokens",
    ),
    pytest.param(
        AIMessage(content="Truncated but present", response_metadata={"finish_reason": "length"}),
        "answered",
        "length",
        id="length_with_text_is_answered",
    ),
    pytest.param(AIMessage(content=""), "empty", "", id="empty_string"),
    pytest.param(AIMessage(content=[]), "empty", "", id="empty_list"),
    pytest.param(
        AIMessage(content="", response_metadata={"finish_reason": "OTHER"}), "empty", "OTHER", id="gemini_other"
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"finish_reason": "MALFORMED_FUNCTION_CALL"}),
        "empty",
        "MALFORMED_FUNCTION_CALL",
        id="gemini_malformed_function_call",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"model_provider": "google_vertexai", "model_name": "gemini"}),
        "empty",
        "",
        id="vertex_prompt_block_has_no_signal",
    ),
    pytest.param(
        AIMessage(content="", response_metadata={"is_blocked": True}),
        "content_filter",
        "is_blocked",
        id="vertex_is_blocked_with_no_other_signal",
    ),
    pytest.param(
        AIMessage(content=[], response_metadata={"status": "incomplete", "incomplete_details": {}}),
        "empty",
        "",
        id="openai_responses_incomplete_with_no_reason",
    ),
]


@pytest.mark.parametrize(("message", "kind", "reason"), CASES)
def test_classify_turn(message, kind, reason):
    outcome = classify_turn(message)

    assert outcome.kind == kind
    assert outcome.provider_reason == reason


def test_detail_carries_filter_signals_and_never_text():
    message = AIMessage(
        content="",
        response_metadata={
            "finish_reason": "SAFETY",
            "safety_ratings": [{"category": "HARM_CATEGORY_HATE_SPEECH", "probability": "HIGH", "blocked": True}],
        },
    )

    assert classify_turn(message).detail == {
        "safety_ratings": [{"category": "HARM_CATEGORY_HATE_SPEECH", "probability": "HIGH", "blocked": True}]
    }


def test_detail_carries_anthropic_stop_details():
    message = AIMessage(content="", response_metadata={"stop_reason": "refusal", "stop_details": {"type": "x"}})

    assert classify_turn(message).detail == {"stop_details": {"type": "x"}}


def test_detail_is_empty_for_an_ordinary_turn():
    assert classify_turn(AIMessage(content="Hi")).detail == {}


def test_outcome_is_immutable():
    with pytest.raises(AttributeError):
        TurnOutcome("answered").kind = "empty"  # ty: ignore[invalid-assignment]


def test_refusal_text_reads_both_openai_shapes():
    assert refusal_text(AIMessage(content="", additional_kwargs={"refusal": "no"})) == "no"
    assert refusal_text(AIMessage(content=[{"type": "refusal", "refusal": "no", "id": "m"}])) == "no"
    assert refusal_text(AIMessage(content="hi")) == ""


def test_refusal_text_skips_a_leading_non_refusal_block():
    message = AIMessage(content=[{"type": "text", "text": "partial"}, {"type": "refusal", "refusal": "no"}])

    assert refusal_text(message) == "no"


def test_provider_reason_for_responses_api_incomplete():
    message = AIMessage(
        content=[], response_metadata={"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}
    )

    assert provider_reason(message) == "content_filter"


class TestRaiseForOutcome:
    def test_answered_returns(self):
        assert raise_for_outcome(TurnOutcome("answered"), node_name="LLM") is None

    @pytest.mark.parametrize("kind", ["refusal", "content_filter"])
    def test_refused_and_filtered_are_participant_actionable(self, kind):
        with pytest.raises(ModelRefusedTurnError) as exc_info:
            raise_for_outcome(TurnOutcome(kind, "SAFETY", {"safety_ratings": []}), node_name="LLM")

        assert exc_info.value.kind == kind
        assert exc_info.value.provider_reason == "SAFETY"
        assert exc_info.value.detail == {"safety_ratings": []}

    def test_length_is_team_actionable_and_names_the_node(self):
        with pytest.raises(ProviderConfigurationError) as exc_info:
            raise_for_outcome(TurnOutcome("length", "max_tokens"), node_name="Answer node")

        text = str(exc_info.value)
        assert "ran out of output tokens" in text
        assert "Node: Answer node." in text
        assert "Stop reason: max_tokens." in text

    def test_empty_is_a_plain_fault(self):
        with pytest.raises(EmptyModelResponseError) as exc_info:
            raise_for_outcome(TurnOutcome("empty", "OTHER"), node_name="LLM")

        assert not isinstance(exc_info.value, ProviderConfigurationError)
        assert exc_info.value.provider_reason == "OTHER"
