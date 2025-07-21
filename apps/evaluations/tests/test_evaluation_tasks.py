from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.evaluations.models import EvaluationResult
from apps.evaluations.tasks import _run_bot_generation, evaluate_single_message_task
from apps.experiments.models import ExperimentSession, Participant
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.langchain import build_fake_llm_service


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory()


@pytest.fixture()
def hardcoded_experiment(team_with_users, db):
    """Create the hardcoded experiment that the bot should use"""
    return ExperimentFactory(
        public_id="abcbaf2c-c5a5-4ba6-802a-83a1e825d762", team=team_with_users, name="Bot Generation Experiment"
    )


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
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_run_bot_generation(get_llm_service, hardcoded_experiment, evaluation_message, team_with_users):
    """Test that _run_bot_generation calls the bot correctly"""
    service = build_fake_llm_service(responses=["Bot generated response"], token_counts=[30])
    get_llm_service.return_value = service

    result = _run_bot_generation(team_with_users, evaluation_message)

    assert result == "Bot generated response"

    evaluation_channel = ExperimentChannel.objects.get(team=team_with_users, platform=ChannelPlatform.EVALUATIONS)
    assert evaluation_channel.platform == ChannelPlatform.EVALUATIONS

    participant = Participant.objects.get(identifier="evaluations", team=team_with_users)
    assert participant.name == "Evaluations Bot"
    assert participant.platform == "evaluations"

    session = ExperimentSession.objects.get(team=team_with_users)
    assert session.experiment == hardcoded_experiment
    assert session.experiment_channel == evaluation_channel
    assert session.participant.identifier == "evaluations"
    assert session.team == team_with_users

    assert session.chat is not None
    assert session.chat.team == team_with_users


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
@patch("apps.evaluations.models.Evaluator.run")
def test_evaluate_single_message_with_bot_generation(
    evaluator_run_mock, get_llm_service, hardcoded_experiment, evaluation_run, evaluation_message
):
    """Test that evaluate_single_message calls bot generation before evaluation"""

    run, evaluator = evaluation_run

    service = build_fake_llm_service(responses=["Bot generated response"], token_counts=[30])
    get_llm_service.return_value = service

    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"score": 0.8}))

    # Run the evaluation task
    evaluate_single_message_task(run.id, [evaluator.id], evaluation_message.id)

    # Verify evaluator was called with message and bot response
    evaluator_run_mock.assert_called_once_with(evaluation_message, "Bot generated response")

    # Verify result was created
    result = EvaluationResult.objects.get(message=evaluation_message, run=run, evaluator=evaluator)
    assert result.output == {"score": 0.8}


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_evaluation_message")
@patch("apps.evaluations.models.Evaluator.run")
def test_evaluate_single_message_handles_bot_generation_error(
    evaluator_run_mock, handle_evaluation_message_mock, hardcoded_experiment, evaluation_run, evaluation_message
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
def test_run_bot_generation_creates_evaluations_participant(hardcoded_experiment, evaluation_message, team_with_users):
    """Test that _run_bot_generation creates the evaluations participant if it doesn't exist"""

    # Verify participant doesn't exist initially
    assert not Participant.objects.filter(identifier="evaluations", team=team_with_users).exists()

    # Run bot generation
    _run_bot_generation(team_with_users, evaluation_message)

    # Verify participant was created
    participant = Participant.objects.get(identifier="evaluations", team=team_with_users)
    assert participant.name == "Evaluations Bot"
    assert participant.platform == "evaluations"

    # Run again - should get the same participant
    _run_bot_generation(team_with_users, evaluation_message)

    # Should still be only one participant
    assert Participant.objects.filter(identifier="evaluations", team=team_with_users).count() == 1


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_run_bot_generation_creates_session(get_llm_service, hardcoded_experiment, evaluation_message, team_with_users):
    """Test that _run_bot_generation creates a session"""

    # Mock the LLM service
    service = build_fake_llm_service(responses=["Bot response"], token_counts=[30])
    get_llm_service.return_value = service

    # Call the bot generation function
    _run_bot_generation(team_with_users, evaluation_message)

    # Verify session was created
    sessions = ExperimentSession.objects.filter(team=team_with_users)
    assert sessions.count() == 1
    session = sessions.first()

    # Verify session has correct properties (like API pattern)
    assert session.experiment == hardcoded_experiment
    assert session.participant.identifier == "evaluations"
    assert session.participant.platform == "evaluations"
    assert session.experiment_channel.platform == ChannelPlatform.EVALUATIONS
    assert session.chat is not None
    assert session.chat.team == team_with_users

    # Verify session was created
    sessions = ExperimentSession.objects.filter(team=team_with_users)
    assert sessions.count() == 1
    session = sessions.first()
