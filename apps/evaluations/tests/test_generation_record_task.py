"""The generation record an evaluation run hands its evaluators: what the bot did, read off the run itself."""

import textwrap
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from apps.evaluations.generation import GenerationRecord
from apps.evaluations.models import EvaluationRun
from apps.evaluations.tasks import evaluate_message, run_bot_generation
from apps.experiments.models import AgentTools, Participant, ParticipantData
from apps.pipelines.tests.utils import (
    code_node,
    create_pipeline_model,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.langchain import build_fake_llm_service

TAGGING_CODE = textwrap.dedent("""
    def main(input, **kwargs):
        add_message_tag("urgent")
        add_session_tag("needs-review")
        set_participant_data_key("plan", "pro")
        set_participant_data_key("name", "Ada")
        return "tagged: " + input
    """)

READ_TAGS_CODE = textwrap.dedent("""
    def main(input, output, context, full_history, generated_response, **kwargs):
        return {"tags": kwargs["generation"]["message_tags"]}
    """)


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def tagging_experiment(team):
    experiment = ChatbotFactory.create(team=team)
    create_pipeline_model(
        [start_node(), code_node(TAGGING_CODE, name="Tagger"), end_node()], pipeline=experiment.pipeline
    )
    experiment.pipeline.save()
    return experiment


@pytest.fixture()
def message(team):
    message = EvaluationMessageFactory.create(
        input={"content": "hello", "role": "human"}, participant_data={"plan": "free"}
    )
    EvaluationDatasetFactory.create(team=team, messages=[message])
    return message


def _python_evaluator(team, code):
    return EvaluatorFactory.create(
        team=team, type="PythonEvaluator", params={"code": code}, llm_provider=None, llm_provider_model=None
    )


@pytest.mark.django_db()
def test_record_carries_what_the_run_did(team, tagging_experiment, message):
    generation = run_bot_generation(
        team, message, tagging_experiment, evaluation_run=EvaluationRunFactory.create(team=team)
    )

    record = generation.record
    assert generation.response == "tagged: hello"
    assert record.message_tags == ["urgent"]
    assert record.session_tags == ["needs-review"]
    assert ("change", "plan", ("free", "pro")) in record.participant_data_diff
    assert record.node_outputs["Tagger"] == "tagged: hello"
    assert [current for _previous, current, _next in record.path] == ["start", "Tagger", "end"]


@pytest.mark.django_db()
def test_record_carries_the_tool_calls_of_an_llm_node(team, message):
    provider = LlmProviderFactory.create(team=team)
    model = LlmProviderModelFactory.create(team=team, type=provider.type)
    experiment = ChatbotFactory.create(team=team)
    create_pipeline_model(
        [
            start_node(),
            llm_response_with_prompt_node(
                str(provider.id), str(model.id), tools=[AgentTools.CALCULATOR.value], name="Agent"
            ),
            end_node(),
        ],
        pipeline=experiment.pipeline,
    )
    experiment.pipeline.save()
    calculate = {"name": AgentTools.CALCULATOR.value, "args": {"expression": "2+2"}, "id": "call_1"}
    responses = [AIMessage(content="", tool_calls=[calculate]), AIMessage(content="Done")]

    with patch(
        "apps.service_providers.models.LlmProvider.get_llm_service",
        return_value=build_fake_llm_service(responses=responses),
    ):
        generation = run_bot_generation(
            team, message, experiment, evaluation_run=EvaluationRunFactory.create(team=team)
        )

    record = generation.record
    assert generation.response == "Done"
    assert record.tool_calls[0]["name"] == AgentTools.CALCULATOR.value
    assert record.tool_calls[0]["node"] == "Agent"
    assert record.llm_calls
    assert all(call["node"] == "Agent" for call in record.llm_calls)


@pytest.mark.django_db()
def test_run_leaves_the_shared_participant_untouched(team, tagging_experiment, message):
    run_bot_generation(team, message, tagging_experiment, evaluation_run=EvaluationRunFactory.create(team=team))

    participant = Participant.objects.get(identifier="evaluations", team=team)
    assert participant.name == "Evaluations Bot"
    stored = [row.data for row in ParticipantData.objects.filter(participant=participant)]
    assert all(data.get("name") != "Ada" for data in stored)


@pytest.mark.django_db()
def test_failed_generation_has_an_empty_record(team, tagging_experiment, message):
    with patch("apps.evaluations.tasks.handle_evaluation_message", side_effect=RuntimeError("boom")):
        generation = run_bot_generation(
            team, message, tagging_experiment, evaluation_run=EvaluationRunFactory.create(team=team)
        )

    assert generation.error is not None
    assert generation.record == GenerationRecord()


@pytest.mark.django_db()
def test_python_evaluator_judges_the_record(team, tagging_experiment, message):
    code = textwrap.dedent("""
        def main(input, output, context, full_history, generated_response, **kwargs):
            generation = kwargs["generation"]
            changed = [op[1] for op in generation["participant_data_diff"] if op[0] == "change"]
            return {"urgent": "urgent" in generation["message_tags"], "upgraded": "plan" in changed}
        """)
    evaluator = _python_evaluator(team, code)
    config = EvaluationConfigFactory.create(
        team=team, dataset=message.evaluationdataset_set.first(), evaluators=[evaluator]
    )
    run = EvaluationRun.objects.create(team=team, config=config, generation_experiment=tagging_experiment)

    evaluate_message(run.id, [evaluator.id], message.id)

    assert run.results.get().output["result"] == {"urgent": True, "upgraded": True}


@pytest.mark.django_db()
def test_no_generation_experiment_supplies_an_empty_record(team, message):
    evaluator = _python_evaluator(team, READ_TAGS_CODE)
    config = EvaluationConfigFactory.create(
        team=team, dataset=message.evaluationdataset_set.first(), evaluators=[evaluator]
    )
    run = EvaluationRun.objects.create(team=team, config=config)

    evaluate_message(run.id, [evaluator.id], message.id)

    assert run.results.get().output["result"] == {"tags": []}
