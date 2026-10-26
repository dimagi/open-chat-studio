"""Span selectors applied to the generation of an evaluation run, end to end through `evaluate_message`."""

import textwrap
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from apps.evaluations.export import annotate_export_fields, build_evaluation_table_data
from apps.evaluations.tests.generation_helpers import _llm_experiment, _python_evaluator, _run_with
from apps.experiments.models import AgentTools
from apps.pipelines.tests.utils import (
    code_node,
    create_pipeline_model,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.tests.langchain import build_fake_llm_service

READ_SPAN_CODE = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        return {"span": kwargs["span"]}
    """)

TAGGER_CODE = textwrap.dedent("""
    def main(input, **kwargs):
        return "tagged: " + input
    """)

FINISHING_CODE = textwrap.dedent("""
    def main(input, **kwargs):
        return "final: " + input
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


TAMPER_CODE = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        kwargs["span"]["input"]["expression"] = "tampered"
        return {}
    """)

READ_EXPRESSION_CODE = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        return {
            "expression": kwargs["span"]["input"]["expression"],
            "record": kwargs["generation"]["tool_calls"][0]["input"]["expression"],
        }
    """)


@pytest.mark.django_db()
def test_an_evaluator_changing_its_span_does_not_change_what_the_next_one_sees(team_with_users, message):
    experiment = _calculator_experiment(team_with_users)
    tamperer = _python_evaluator(team_with_users, TAMPER_CODE, span_type="tool", span_name="calculator")
    reader = _python_evaluator(team_with_users, READ_EXPRESSION_CODE, span_type="tool", span_name="calculator")

    run = _run_with(
        team_with_users,
        message,
        experiment,
        tamperer,
        reader,
        responses=[AIMessage(content="", tool_calls=[CALCULATE]), AIMessage(content="Done")],
    )

    assert run.results.get(evaluator=reader).output["result"] == {"expression": "2+2", "record": "2+2"}


@pytest.fixture()
def two_code_node_experiment(team_with_users):
    experiment = ChatbotFactory.create(team=team_with_users)
    create_pipeline_model(
        [start_node(), code_node(TAGGER_CODE, name="Tagger"), code_node(FINISHING_CODE, name="Finisher"), end_node()],
        pipeline=experiment.pipeline,
    )
    experiment.pipeline.save()
    return experiment


JUDGE_RESPONSE_CODE = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        return {"judged": generated_response}
    """)


@pytest.mark.django_db()
def test_a_node_span_as_response_replaces_the_generated_response(team_with_users, two_code_node_experiment, message):
    evaluator = _python_evaluator(
        team_with_users, JUDGE_RESPONSE_CODE, span_type="node", span_name="Tagger", span_as_response=True
    )

    run = _run_with(team_with_users, message, two_code_node_experiment, evaluator)

    result = run.results.get()
    assert result.output["result"] == {"judged": "tagged: hello"}
    assert result.output["generated_response"] == "final: tagged: hello"
    assert result.output["judged_response"] == "tagged: hello"


@pytest.mark.django_db()
def test_the_generated_response_column_stays_the_reply_with_mixed_evaluators(
    team_with_users, two_code_node_experiment, message
):
    span_evaluator = _python_evaluator(
        team_with_users, JUDGE_RESPONSE_CODE, span_type="node", span_name="Tagger", span_as_response=True
    )
    plain_evaluator = _python_evaluator(team_with_users, JUDGE_RESPONSE_CODE)
    assert span_evaluator.id < plain_evaluator.id

    run = _run_with(team_with_users, message, two_code_node_experiment, span_evaluator, plain_evaluator)

    assert run.results.get(evaluator=plain_evaluator).output["judged_response"] is None
    [row] = build_evaluation_table_data(annotate_export_fields(run.results.order_by("created_at")))
    assert row["Generated Response"] == "final: tagged: hello"


@pytest.mark.django_db()
def test_without_a_selector_the_generated_response_is_the_final_reply(
    team_with_users, two_code_node_experiment, message
):
    evaluator = _python_evaluator(team_with_users, JUDGE_RESPONSE_CODE)

    run = _run_with(team_with_users, message, two_code_node_experiment, evaluator)

    result = run.results.get()
    assert result.output["result"] == {"judged": "final: tagged: hello"}
    assert result.output["generated_response"] == "final: tagged: hello"


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
