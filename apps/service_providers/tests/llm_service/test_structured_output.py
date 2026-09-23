import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, ValidationError

from apps.service_providers.llm_service.outcomes import provider_reason
from apps.service_providers.llm_service.structured_output import (
    NoStructuredOutputError,
    structured_output_runnable,
    unwrap_structured_output,
)
from apps.utils.tests.langchain import FakeLlmSimpleTokenCount


class Verdict(BaseModel):
    sentiment: str


class _NoToolCallUnderThinking(FakeLlmSimpleTokenCount):
    def with_structured_output(self, schema, **kwargs):
        def _raise(_input):
            raise OutputParserException("structured output via forced tool calling is not guaranteed")

        return RunnableLambda(_raise)


def _invoke(reply: AIMessage):
    llm = FakeLlmSimpleTokenCount(responses=[reply])
    return structured_output_runnable(llm, Verdict).invoke("judge this")


def test_returns_the_parsed_model_when_the_tool_is_called():
    reply = AIMessage(content="", tool_calls=[{"name": "Verdict", "args": {"sentiment": "ok"}, "id": "1"}])

    assert _invoke(reply) == Verdict(sentiment="ok")


@pytest.mark.parametrize(
    ("reply", "model_text", "reason"),
    [
        pytest.param(AIMessage(content="I can't help with that."), "I can't help with that.", "", id="prose-reply"),
        pytest.param(
            AIMessage(content=[], response_metadata={"stop_reason": "refusal"}),
            "",
            "refusal",
            id="anthropic-stop-reason",
        ),
        pytest.param(
            AIMessage(content="", response_metadata={"finish_reason": "content_filter"}),
            "",
            "content_filter",
            id="openai-finish-reason",
        ),
        pytest.param(
            AIMessage(content=[{"type": "refusal", "refusal": "declined"}]),
            "declined",
            "refusal",
            id="responses-api-refusal-block",
        ),
    ],
)
def test_raises_when_the_model_returns_no_structured_result(reply, model_text, reason):
    with pytest.raises(NoStructuredOutputError) as exc_info:
        _invoke(reply)

    assert exc_info.value.model_text == model_text
    assert exc_info.value.reason == reason
    assert not isinstance(exc_info.value, ValueError)


def test_error_message_includes_the_model_text():
    with pytest.raises(NoStructuredOutputError, match="accuracy_result: inaccurate"):
        _invoke(AIMessage(content="", additional_kwargs={"refusal": "accuracy_result: inaccurate"}))


def test_schema_mismatch_is_reraised_so_callers_can_retry_it():
    reply = AIMessage(content="", tool_calls=[{"name": "Verdict", "args": {"wrong": 1}, "id": "1"}])

    with pytest.raises(ValidationError):
        _invoke(reply)


def test_a_refusal_wins_over_the_parsing_error_it_caused():
    raw = AIMessage(content="", additional_kwargs={"refusal": "declined"})

    with pytest.raises(NoStructuredOutputError) as exc_info:
        unwrap_structured_output({"raw": raw, "parsed": None, "parsing_error": RuntimeError("refused")})

    assert exc_info.value.model_text == "declined"


def test_provider_reason_reads_the_provider_metadata():
    assert provider_reason(AIMessage(content="", response_metadata={"stop_reason": "refusal"})) == "refusal"
    assert provider_reason(AIMessage(content="no metadata")) == ""


def test_provider_reason_reads_responses_api_incomplete_details():
    message = AIMessage(
        content=[], response_metadata={"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}
    )

    assert provider_reason(message) == "content_filter"


def test_a_missing_tool_call_raised_by_the_provider_adapter_is_no_structured_output():
    llm = _NoToolCallUnderThinking(responses=["unused"])

    with pytest.raises(NoStructuredOutputError) as exc_info:
        structured_output_runnable(llm, Verdict).invoke("judge this")

    assert exc_info.value.reason == "no tool call"
    assert exc_info.value.model_text == ""


def test_a_parsing_error_is_still_reraised_when_it_is_an_output_parser_exception():
    raw = AIMessage(content="not json")

    with pytest.raises(OutputParserException):
        unwrap_structured_output(
            {"raw": raw, "parsed": None, "parsing_error": OutputParserException("Invalid json output")}
        )
