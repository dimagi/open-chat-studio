"""Span selectors applied to the generation of an evaluation run, end to end through `evaluate_message`."""

import textwrap
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from apps.evaluations.tests.generation_helpers import _llm_experiment, _python_evaluator, _run_with
from apps.experiments.models import AgentTools
from apps.pipelines.tests.utils import end_node, llm_response_with_prompt_node, start_node
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.tests.langchain import build_fake_llm_service

READ_SPAN_CODE = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        return {"span": kwargs["span"]}
    """)

CALCULATE = {"name": AgentTools.CALCULATOR.value, "args": {"expression": "2+2"}, "id": "call_1"}


def _calculator_experiment(team):
    return _llm_experiment(
        team,
        lambda provider_id, model_id: [
            start_node(),
            llm_response_with_prompt_node(provider_id, model_id, tools=[AgentTools.CALCULATOR.value], name="Agent"),
            end_node(),
        ],
    )


@pytest.mark.django_db()
def test_a_python_evaluator_judges_a_tool_span(team_with_users, message):
    experiment = _calculator_experiment(team_with_users)
    code = textwrap.dedent("""
        def main(input, output, context, full_history, generated_response, **kwargs):
            return {"expression": kwargs["span"]["input"]["expression"], "answer": kwargs["span"]["output"]}
        """)
    evaluator = _python_evaluator(team_with_users, code, span_type="tool", span_name="calculator")

    run = _run_with(
        team_with_users,
        message,
        experiment,
        evaluator,
        responses=[AIMessage(content="", tool_calls=[CALCULATE]), AIMessage(content="Done")],
    )

    assert run.results.get().output["result"] == {"expression": "2+2", "answer": "4"}


@pytest.mark.django_db()
def test_a_node_span_as_response_replaces_the_generated_response(team_with_users, tagging_experiment, message):
    code = textwrap.dedent("""
        def main(input, output, context, full_history, generated_response, **kwargs):
            return {"judged": generated_response}
        """)
    evaluator = _python_evaluator(team_with_users, code, span_type="node", span_name="Tagger", span_as_response=True)

    run = _run_with(team_with_users, message, tagging_experiment, evaluator)

    result = run.results.get()
    assert result.output["result"] == {"judged": "tagged: hello"}
    assert result.output["generated_response"] == "tagged: hello"


@pytest.mark.django_db()
def test_a_selector_with_no_match_is_an_error_result(team_with_users, tagging_experiment, message):
    evaluator = _python_evaluator(team_with_users, READ_SPAN_CODE, span_type="tool", span_name="file-search")

    run = _run_with(team_with_users, message, tagging_experiment, evaluator)

    output = run.results.get().output
    assert "No tool span named 'file-search'" in output["error"]
    assert "Available tool spans: none" in output["error"]


@pytest.mark.django_db()
def test_an_llm_evaluator_judges_a_tool_span(team_with_users, message):
    experiment = _calculator_experiment(team_with_users)
    judge_answer = AIMessage(
        content="", tool_calls=[{"name": "DynamicModel", "args": {"verdict": "right"}, "id": "call_j"}]
    )
    evaluator = EvaluatorFactory.create(
        team=team_with_users,
        params={
            "prompt": "Was {span.output} the right answer to {span.input}? Reply: {generated_response}",
            "output_schema": {"verdict": {"type": "string", "description": "verdict"}},
            "span_type": "tool",
            "span_name": "calculator",
        },
    )
    service = build_fake_llm_service(
        responses=[AIMessage(content="", tool_calls=[CALCULATE]), AIMessage(content="Done"), judge_answer]
    )

    with patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service):
        run = _run_with(team_with_users, message, experiment, evaluator)

    result = run.results.get()
    assert result.output["result"] == {"verdict": "right"}
    assert result.output["generated_response"] == "Done"
    judge_prompt = "\n".join(m.content for m in service.llm.get_call_messages()[-1])
    assert 'Was 4 the right answer to {"expression": "2+2"}? Reply: Done' in judge_prompt
