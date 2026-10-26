"""The generation record an evaluation run hands its evaluators: what the bot did, read off the run itself."""

import textwrap
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from apps.evaluations.generation import GenerationRecord
from apps.evaluations.models import EvaluationRun
from apps.evaluations.tasks import evaluate_message
from apps.evaluations.tests.generation_helpers import _generate, _llm_experiment, _python_evaluator
from apps.experiments.models import AgentTools, Participant, ParticipantData
from apps.pipelines.tests.utils import (
    code_node,
    end_node,
    llm_response_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.utils.factories.evaluations import EvaluationConfigFactory

RAISING_CODE = textwrap.dedent("""
    def main(input, **kwargs):
        raise ValueError("boom")
    """)

READ_TAGS_CODE = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        return {"tags": kwargs["generation"]["message_tags"]}
    """)


@pytest.mark.django_db()
def test_record_carries_what_the_run_did(team_with_users, tagging_experiment, message):
    generation = _generate(team_with_users, message, tagging_experiment)

    record = generation.record
    assert generation.response == "tagged: hello"
    assert record.message_tags == ["urgent"]
    assert record.session_tags == ["needs-review"]
    assert ("change", "plan", ("free", "pro")) in record.participant_data_diff
    assert record.node_outputs["Tagger"] == "tagged: hello"
    assert [current for _previous, current, _next in record.path] == ["start", "Tagger", "end"]


@pytest.mark.django_db()
def test_record_carries_the_tool_calls_of_an_llm_node(team_with_users, message):
    experiment = _llm_experiment(
        team_with_users,
        lambda provider_id, model_id: [
            start_node(),
            llm_response_with_prompt_node(provider_id, model_id, tools=[AgentTools.CALCULATOR.value], name="Agent"),
            end_node(),
        ],
    )
    calculate = {"name": AgentTools.CALCULATOR.value, "args": {"expression": "2+2"}, "id": "call_1"}

    generation = _generate(
        team_with_users,
        message,
        experiment,
        responses=[AIMessage(content="", tool_calls=[calculate]), AIMessage(content="Done")],
    )

    record = generation.record
    assert generation.response == "Done"
    assert record.tool_calls[0]["name"] == AgentTools.CALCULATOR.value
    assert record.tool_calls[0]["node"] == "Agent"
    assert record.llm_calls
    assert all(call["node"] == "Agent" for call in record.llm_calls)


@pytest.mark.django_db()
def test_record_carries_a_participant_data_tool_call(team_with_users, message):
    experiment = _llm_experiment(
        team_with_users,
        lambda provider_id, model_id: [
            start_node(),
            llm_response_with_prompt_node(
                provider_id,
                model_id,
                prompt="You are a helpful assistant. {participant_data}",
                tools=[AgentTools.UPDATE_PARTICIPANT_DATA.value],
                name="Agent",
            ),
            end_node(),
        ],
    )
    update = {"name": AgentTools.UPDATE_PARTICIPANT_DATA.value, "args": {"key": "plan", "value": "pro"}, "id": "call_1"}

    generation = _generate(
        team_with_users,
        message,
        experiment,
        responses=[AIMessage(content="", tool_calls=[update]), AIMessage(content="Done")],
    )

    record = generation.record
    assert isinstance(record.tool_calls[0]["output"], str)
    assert record.tool_calls[0]["node"] == "Agent"
    assert ("change", "plan", ("free", "pro")) in record.participant_data_diff


@pytest.mark.django_db()
def test_unexpected_failure_keeps_the_calls_made_before_it(team_with_users, message):
    experiment = _llm_experiment(
        team_with_users,
        lambda provider_id, model_id: [
            start_node(),
            llm_response_with_prompt_node(provider_id, model_id, name="Answer"),
            llm_response_with_prompt_node(provider_id, model_id, name="Second"),
            end_node(),
        ],
    )

    generation = _generate(team_with_users, message, experiment, responses=["first", AIMessage(content="")])

    assert generation.error is not None
    assert generation.response == ""
    assert [call["node"] for call in generation.record.llm_calls] == ["Answer", "Second"]
    assert generation.record.node_outputs == {}


@pytest.mark.django_db()
def test_canned_reply_failure_keeps_the_calls_made_before_it(team_with_users, message):
    experiment = _llm_experiment(
        team_with_users,
        lambda provider_id, model_id: [
            start_node(),
            llm_response_node(provider_id, model_id, name="Answer"),
            code_node(RAISING_CODE, name="Guard"),
            end_node(),
        ],
    )

    generation = _generate(team_with_users, message, experiment, responses=["first"])

    assert generation.error is not None
    assert generation.record.llm_calls[0]["node"] == "Answer"


@pytest.mark.django_db()
def test_run_leaves_the_shared_participant_untouched(team_with_users, tagging_experiment, message):
    _generate(team_with_users, message, tagging_experiment)

    participant = Participant.objects.get(identifier="evaluations", team=team_with_users)
    assert participant.name == "Evaluations Bot"
    stored = [row.data for row in ParticipantData.objects.filter(participant=participant)]
    assert all(data.get("name") != "Ada" for data in stored)


@pytest.mark.django_db()
def test_failed_generation_has_an_empty_record(team_with_users, tagging_experiment, message):
    with patch("apps.evaluations.tasks.handle_evaluation_message", side_effect=RuntimeError("boom")):
        generation = _generate(team_with_users, message, tagging_experiment)

    assert generation.error is not None
    assert generation.record == GenerationRecord()


@pytest.mark.django_db()
def test_python_evaluator_judges_the_record(team_with_users, tagging_experiment, message):
    code = textwrap.dedent("""
        def main(input, output, context, full_history, generated_response, **kwargs):
            generation = kwargs["generation"]
            changed = [op[1] for op in generation["participant_data_diff"] if op[0] == "change"]
            return {"urgent": "urgent" in generation["message_tags"], "upgraded": "plan" in changed}
        """)
    evaluator = _python_evaluator(team_with_users, code)
    config = EvaluationConfigFactory.create(
        team=team_with_users, dataset=message.evaluationdataset_set.first(), evaluators=[evaluator]
    )
    run = EvaluationRun.objects.create(team=team_with_users, config=config, generation_experiment=tagging_experiment)

    evaluate_message(run.id, [evaluator.id], message.id)

    assert run.results.get().output["result"] == {"urgent": True, "upgraded": True}


@pytest.mark.django_db()
def test_no_generation_experiment_supplies_an_empty_record(team_with_users, message):
    evaluator = _python_evaluator(team_with_users, READ_TAGS_CODE)
    config = EvaluationConfigFactory.create(
        team=team_with_users, dataset=message.evaluationdataset_set.first(), evaluators=[evaluator]
    )
    run = EvaluationRun.objects.create(team=team_with_users, config=config)

    evaluate_message(run.id, [evaluator.id], message.id)

    assert run.results.get().output["result"] == {"tags": []}
