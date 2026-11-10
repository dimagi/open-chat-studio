from unittest import mock

import pytest
from pydantic import ValidationError

from apps.evaluations.const import unknown_span_variables
from apps.evaluations.evaluators import LlmEvaluator
from apps.evaluations.exceptions import EvaluationRunException
from apps.evaluations.generation import GenerationRecord
from apps.evaluations.tests.llm_evaluator_helpers import OUTPUT_SCHEMA, prompt_sent_to, structured_response
from apps.utils.factories.evaluations import EvaluationMessageFactory
from apps.utils.tests.langchain import build_fake_llm_service

_UNKNOWN_SPAN_FIELDS = ["{span.name}", "{span.output[0]}", "{span.output!r}", "{span}"]


@pytest.mark.parametrize("offending", [pytest.param(field, id=field) for field in _UNKNOWN_SPAN_FIELDS])
def test_llm_evaluator_prompt_unknown_span_variable_raises(offending):
    with pytest.raises(ValidationError) as exc_info:
        LlmEvaluator(
            llm_provider_id=1,
            llm_provider_model_id=1,
            prompt=f"Input: {{input.content}} {offending}",
            output_schema=OUTPUT_SCHEMA,
        )
    error_message = str(exc_info.value)
    assert "Unknown span variable" in error_message
    assert offending in error_message
    assert "{span.input}, {span.output}, {span.error}" in error_message


def test_unknown_span_variables():
    assert unknown_span_variables(" ".join(_UNKNOWN_SPAN_FIELDS)) == {
        "span.name",
        "span.output[0]",
        "span.output!r",
        "span",
    }
    assert unknown_span_variables("{span.input} {span.output} {span.error}") == set()


def test_span_variables_need_a_selector():
    with pytest.raises(ValidationError, match="no span is selected"):
        LlmEvaluator(
            llm_provider_id=1, llm_provider_model_id=1, prompt="Judge {span.output}", output_schema=OUTPUT_SCHEMA
        )


@pytest.mark.parametrize(
    "prompt",
    [
        pytest.param("Judge {span.input}", id="input"),
        pytest.param("Judge {span.output}", id="output"),
        pytest.param("Judge {span.error}", id="error"),
    ],
)
def test_span_variables_are_accepted_with_a_selector(prompt):
    evaluator = LlmEvaluator(
        llm_provider_id=1,
        llm_provider_model_id=1,
        prompt=prompt,
        output_schema=OUTPUT_SCHEMA,
        span_type="tool",
        span_name="file-search",
    )
    assert evaluator.prompt == prompt
    assert evaluator.span_type == "tool"
    assert evaluator.span_name == "file-search"


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_the_selected_span_reaches_the_prompt_and_the_response(get_llm_service, llm_provider, llm_provider_model):
    service = build_fake_llm_service(responses=[structured_response()])
    get_llm_service.return_value = service
    evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt="Chunks: {span.output} for {span.input}",
        output_schema=OUTPUT_SCHEMA,
        span_type="tool",
        span_name="file-search",
        span_as_response=True,
    )
    record = GenerationRecord(
        tool_calls=[
            {"node": "Answer", "name": "file-search", "input": {"query": "q"}, "output": "chunk a", "error": None}
        ]
    )

    result = evaluator.run(EvaluationMessageFactory.create(), "the reply", generation=record)

    assert 'Chunks: chunk a for {"query": "q"}' in prompt_sent_to(service)
    assert result.generated_response == "the reply"
    assert result.judged_response == "chunk a"


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_the_span_error_reaches_the_prompt(get_llm_service, llm_provider, llm_provider_model):
    service = build_fake_llm_service(responses=[structured_response()])
    get_llm_service.return_value = service
    evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt="Did {span.input} fail? Error: {span.error}",
        output_schema=OUTPUT_SCHEMA,
        span_type="tool",
        span_name="file-search",
    )
    record = GenerationRecord(
        tool_calls=[{"node": "Answer", "name": "file-search", "input": "q", "output": None, "error": "timed out"}]
    )

    evaluator.run(EvaluationMessageFactory.create(), "the reply", generation=record)

    assert "Did q fail? Error: timed out" in prompt_sent_to(service)


_FILE_SEARCH_RECORD = GenerationRecord(
    tool_calls=[{"node": "Answer", "name": "file-search", "input": "q", "output": "chunk a", "error": None}]
)


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_generated_response_in_the_prompt_is_the_span_output_when_span_as_response_is_on(
    get_llm_service, llm_provider, llm_provider_model
):
    service = build_fake_llm_service(responses=[structured_response()])
    get_llm_service.return_value = service
    evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt="Judge {generated_response}",
        output_schema=OUTPUT_SCHEMA,
        span_type="tool",
        span_name="file-search",
        span_as_response=True,
    )

    evaluator.run(EvaluationMessageFactory.create(), "the reply", generation=_FILE_SEARCH_RECORD)

    prompt = prompt_sent_to(service)
    assert "Judge chunk a" in prompt
    assert "the reply" not in prompt


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_a_selector_with_no_match_is_an_error_before_the_judge_is_called(
    get_llm_service, llm_provider, llm_provider_model
):
    service = build_fake_llm_service(responses=[structured_response()])
    get_llm_service.return_value = service
    evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt="Judge {span.output}",
        output_schema=OUTPUT_SCHEMA,
        span_type="tool",
        span_name="calculator",
    )

    with pytest.raises(EvaluationRunException, match="No tool span named 'calculator'"):
        evaluator.run(EvaluationMessageFactory.create(), "the reply", generation=_FILE_SEARCH_RECORD)

    assert service.llm.get_calls() == []


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_an_errored_span_is_refused_before_the_judge_is_called(get_llm_service, llm_provider, llm_provider_model):
    service = build_fake_llm_service(responses=[structured_response()])
    get_llm_service.return_value = service
    evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt="Judge {generated_response}",
        output_schema=OUTPUT_SCHEMA,
        span_type="tool",
        span_name="calculator",
        span_as_response=True,
    )
    record = GenerationRecord(
        tool_calls=[
            {
                "node": "Agent",
                "name": "calculator",
                "input": {"expression": "1/0"},
                "output": None,
                "error": "division by zero",
            }
        ]
    )

    with pytest.raises(EvaluationRunException, match="calculator"):
        evaluator.run(EvaluationMessageFactory.create(), "the reply", generation=record)

    assert service.llm.get_calls() == []
