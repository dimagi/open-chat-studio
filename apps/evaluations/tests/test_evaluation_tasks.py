from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.evaluations.models import EvaluationResult, ExperimentVersionSelection
from apps.evaluations.tasks import evaluate_single_message_task, run_bot_generation
from apps.experiments.models import ExperimentSession
from apps.participants.models import Participant

# TODO: Update Participant import
from apps.pipelines.tests.utils import create_pipeline_model, end_node, render_template_node, start_node
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.langchain import build_fake_llm_service


@pytest.fixture()
def team_with_users():
    return TeamWithUsersFactory()


@pytest.fixture()
def experiment(team_with_users, db):
    experiment = ChatbotFactory()
    template_node = render_template_node("I heard: {{input}}")
    create_pipeline_model([start_node(), template_node, end_node()], pipeline=experiment.pipeline)
    experiment.pipeline.save()
    return experiment


@pytest.fixture()
def evaluation_message(team_with_users, db):
    """Create an evaluation message with test data"""
    message = EvaluationMessageFactory(
        input={"content": "What is the weather like?", "role": "human"},
        output={"content": "I cannot check the weather.", "role": "ai"},
        context={"history": []},
    )
    EvaluationDatasetFactory(team=team_with_users, messages=[message])
    return message


@pytest.fixture()
def evaluation_run(evaluation_message, team_with_users, db):
    """Create an evaluation run with config and evaluator"""
    # Get the dataset that contains the message
    dataset = evaluation_message.evaluationdataset_set.first()
    config = EvaluationConfigFactory(team=team_with_users, dataset=dataset)
    evaluator = EvaluatorFactory(team=team_with_users)
    config.evaluators.add(evaluator)
    return EvaluationRunFactory(config=config, team=team_with_users), evaluator


@pytest.mark.django_db()
def test_run_bot_generation(experiment, evaluation_message, team_with_users):
    """Test that _run_bot_generation calls the bot correctly"""
    session_id, result = run_bot_generation(team_with_users, evaluation_message, experiment)

    assert result == "I heard: " + evaluation_message.input["content"]

    evaluation_channel = ExperimentChannel.objects.get(team=team_with_users, platform=ChannelPlatform.EVALUATIONS)
    assert evaluation_channel.platform == ChannelPlatform.EVALUATIONS

    participant = Participant.objects.get(identifier="evaluations", team=team_with_users)
    assert participant.name == "Evaluations Bot"
    assert participant.platform == "evaluations"

    session = ExperimentSession.objects.get(team=team_with_users, id=session_id)
    assert session.experiment == experiment
    assert session.experiment_channel == evaluation_channel
    assert session.participant.identifier == "evaluations"

    assert session.chat is not None
    assert session.chat.team == team_with_users


@pytest.mark.django_db()
def test_run_bot_generation_with_participant_data_session_state(evaluation_message, team_with_users):
    """Test that _run_bot_generation calls the bot correctly"""
    experiment = ChatbotFactory()
    template_node = render_template_node("{{participant_data}}:{{session_state}}")
    create_pipeline_model([start_node(), template_node, end_node()], pipeline=experiment.pipeline)
    experiment.pipeline.save()

    evaluation_message.participant_data = {"test_pd": "demo_pd"}
    evaluation_message.session_state = {"test_ss": "demo_ss"}
    session_id, result = run_bot_generation(team_with_users, evaluation_message, experiment)

    data = {"name": "Evaluations Bot"} | evaluation_message.participant_data
    assert result == f"{data}:{evaluation_message.session_state}"


@pytest.mark.django_db()
@patch("apps.evaluations.models.Evaluator.run")
def test_evaluate_single_message_with_bot_generation(
    evaluator_run_mock, experiment, evaluation_run, evaluation_message
):
    """Test that evaluate_single_message calls bot generation before evaluation"""

    run, evaluator = evaluation_run
    config = run.config
    config.version_selection_type = ExperimentVersionSelection.SPECIFIC
    config.experiment_version = experiment
    config.save()

    run.generation_experiment = config.get_generation_experiment_version()
    run.save()

    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"score": 0.8}))

    # Run the evaluation task
    evaluate_single_message_task(run.id, [evaluator.id], evaluation_message.id)

    # Verify evaluator was called with message and bot response
    expected = "I heard: " + evaluation_message.input["content"]
    evaluator_run_mock.assert_called_once_with(evaluation_message, expected)

    # Verify result was created
    result = EvaluationResult.objects.get(message=evaluation_message, run=run, evaluator=evaluator)
    assert result.output == {"score": 0.8}


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_evaluation_message")
@patch("apps.evaluations.models.Evaluator.run")
def test_evaluate_single_message_handles_bot_generation_error(
    evaluator_run_mock, handle_evaluation_message_mock, evaluation_run, evaluation_message
):
    """Test that evaluation continues even if bot generation fails"""
    run, evaluator = evaluation_run

    # Mock bot generation failure
    handle_evaluation_message_mock.side_effect = Exception("Bot generation failed")
    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"score": 0.8}))

    # Run the evaluation task - should not fail
    evaluate_single_message_task(run.id, [evaluator.id], evaluation_message.id)

    # Verify evaluator was still called despite bot error, with empty string response since bot failed
    evaluator_run_mock.assert_called_once_with(evaluation_message, "")

    # Verify result was still created
    result = EvaluationResult.objects.get(message=evaluation_message, run=run, evaluator=evaluator)
    assert result.output == {"score": 0.8}


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_run_bot_generation_creates_evaluations_participant(
    get_llm_service, experiment, evaluation_message, team_with_users
):
    """Test that _run_bot_generation creates the evaluations participant if it doesn't exist"""
    service = build_fake_llm_service(responses=["Bot generated response"], token_counts=[30])
    get_llm_service.return_value = service

    # Verify participant doesn't exist initially
    assert not Participant.objects.filter(identifier="evaluations", team=team_with_users).exists()

    # Run bot generation
    run_bot_generation(team_with_users, evaluation_message, experiment)

    # Verify participant was created
    participant = Participant.objects.get(identifier="evaluations", team=team_with_users)
    assert participant.name == "Evaluations Bot"
    assert participant.platform == "evaluations"

    # Run again - should get the same participant
    run_bot_generation(team_with_users, evaluation_message, experiment)

    # Should still be only one participant
    assert Participant.objects.filter(identifier="evaluations", team=team_with_users).count() == 1


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_run_bot_generation_creates_session(get_llm_service, experiment, evaluation_message, team_with_users):
    """Test that _run_bot_generation creates a session"""

    # Mock the LLM service
    service = build_fake_llm_service(responses=["Bot response"], token_counts=[30])
    get_llm_service.return_value = service

    # Call the bot generation function
    run_bot_generation(team_with_users, evaluation_message, experiment)

    # Verify session was created
    sessions = ExperimentSession.objects.filter(team=team_with_users)
    assert sessions.count() == 1
    session = sessions.first()

    # Verify session has correct properties (like API pattern)
    assert session.experiment == experiment
    assert session.participant.identifier == "evaluations"
    assert session.participant.platform == "evaluations"
    assert session.experiment_channel.platform == ChannelPlatform.EVALUATIONS
    assert session.chat is not None
    assert session.chat.team == team_with_users

    # Verify session was created
    sessions = ExperimentSession.objects.filter(team=team_with_users)
    assert sessions.count() == 1
    session = sessions.first()
