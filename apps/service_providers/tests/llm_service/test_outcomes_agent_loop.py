"""Pin that each adapter's stop signal survives the response_metadata merge and reaches classify_turn intact."""

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from apps.pipelines.nodes.llm_node import _get_final_ai_message
from apps.service_providers.llm_service.outcomes import classify_turn


class _ShapeModel(BaseChatModel):
    """Returns one fixed ChatResult, shaped like a given adapter's non-streaming output."""

    message: AIMessage
    generation_info: dict[str, Any] = {}
    llm_output: dict[str, Any] | None = None

    @property
    def _llm_type(self) -> str:
        return "shape"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        generation = ChatGeneration(message=self.message.model_copy(), generation_info=dict(self.generation_info))
        return ChatResult(generations=[generation], llm_output=self.llm_output)


def _run(model: _ShapeModel) -> AIMessage:
    agent = create_agent(model=model, tools=[])
    result = agent.invoke({"messages": [HumanMessage(content="hi")]})
    return _get_final_ai_message(result["messages"])


SHAPES = [
    pytest.param(
        _ShapeModel(
            message=AIMessage(content="", additional_kwargs={"refusal": "no"}),
            generation_info={"finish_reason": "stop", "logprobs": None},
            llm_output={"token_usage": {}, "model_name": "gpt-4o", "model_provider": "openai"},
        ),
        "refusal",
        id="openai_chat_completions_refusal",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(content="", additional_kwargs={"refusal": None}),
            generation_info={"finish_reason": "content_filter", "logprobs": None},
            llm_output={"token_usage": {}, "model_name": "gpt-4o", "model_provider": "openai"},
        ),
        "content_filter",
        id="openai_chat_completions_filter",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(
                content=[{"type": "refusal", "refusal": "no", "id": "msg_1"}],
                response_metadata={"id": "resp_1", "status": "completed", "model_provider": "openai"},
            ),
        ),
        "refusal",
        id="openai_responses_refusal",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(
                content=[],
                response_metadata={
                    "id": "resp_1",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "content_filter"},
                    "model_provider": "openai",
                },
            ),
        ),
        "content_filter",
        id="openai_responses_filter",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(content="Partial"),
            llm_output={"id": "msg_1", "model": "claude", "stop_reason": "refusal", "stop_sequence": None},
        ),
        "refusal",
        id="anthropic_refusal_via_llm_output",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(content=""),
            llm_output={"id": "msg_1", "model": "claude", "stop_reason": "max_tokens", "stop_sequence": None},
        ),
        "length",
        id="anthropic_max_tokens_via_llm_output",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(content="", response_metadata={"model_provider": "google_genai"}),
            generation_info={
                "finish_reason": "SAFETY",
                "model_name": "gemini-2.5-flash",
                "safety_ratings": [{"category": "HARM_CATEGORY_HATE_SPEECH", "probability": "HIGH", "blocked": True}],
            },
            llm_output={"prompt_feedback": {"block_reason": 0, "safety_ratings": []}},
        ),
        "content_filter",
        id="gemini_candidate_block_via_generation_info",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(content=""),
            generation_info={},
            llm_output={"prompt_feedback": {"block_reason": 1, "safety_ratings": [{"category": 9}]}},
        ),
        "content_filter",
        id="gemini_prompt_block_via_llm_output",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(content="", response_metadata={"model_provider": "google_vertexai"}),
            generation_info={"is_blocked": True, "finish_reason": "SAFETY", "finish_message": "blocked by safety"},
        ),
        "content_filter",
        id="vertex_candidate_block",
    ),
    pytest.param(
        _ShapeModel(
            message=AIMessage(content=[], response_metadata={"model_provider": "google_genai"}),
            generation_info={"finish_reason": "MALFORMED_FUNCTION_CALL", "safety_ratings": []},
        ),
        "empty",
        id="gemini_malformed_function_call",
    ),
]


@pytest.mark.parametrize(("model", "expected_kind"), SHAPES)
def test_adapter_shape_survives_the_agent_loop(model, expected_kind):
    final = _run(model)

    assert classify_turn(final).kind == expected_kind


def test_agent_loop_ends_on_an_empty_message_and_returns_it_last():
    agent = create_agent(model=_ShapeModel(message=AIMessage(content="")), tools=[])

    result = agent.invoke({"messages": [HumanMessage(content="hi")]})

    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].text == ""
