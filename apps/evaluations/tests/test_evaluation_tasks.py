from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.evaluations.models import EvaluationResult
from apps.evaluations.tasks import _run_bot_generation, run_single_evaluation_task
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
@patch("apps.channels.tasks.handle_evaluation_message")
def test_run_bot_generation(handle_evaluation_message_mock, hardcoded_experiment, evaluation_message, team_with_users):
    """Test that _run_bot_generation calls the bot correctly"""
    from apps.chat.models import ChatMessage

    # Mock the bot response
    mock_response = ChatMessage(content="Bot generated response")
    handle_evaluation_message_mock.return_value = mock_response

    # Call the bot generation function
    _run_bot_generation(team_with_users, evaluation_message)

    # Verify the bot was called correctly
    handle_evaluation_message_mock.assert_called_once()
    args, kwargs = handle_evaluation_message_mock.call_args

    assert kwargs["user"] is None
    assert kwargs["experiment_version"] == hardcoded_experiment
    assert kwargs["message_text"] == "What is the weather like?"
    assert kwargs["participant_id"] == "evaluations"

    # Verify evaluation channel was created
    evaluation_channel = ExperimentChannel.objects.get(team=team_with_users, platform=ChannelPlatform.EVALUATIONS)
    assert kwargs["experiment_channel"] == evaluation_channel

    # Verify session was created and passed to handle_evaluation_message
    assert "session" in kwargs
    session = kwargs["session"]
    assert isinstance(session, ExperimentSession)
    assert session.experiment == hardcoded_experiment
    assert session.experiment_channel == evaluation_channel
    assert session.participant.identifier == "evaluations"
    assert session.team == team_with_users

    # Verify evaluations participant was created
    participant = Participant.objects.get(identifier="evaluations", team=team_with_users)
    assert participant.name == "Evaluations Bot"
    assert participant.platform == "evaluations"

    # Verify chat was created for the session
    assert session.chat is not None
    assert session.chat.team == team_with_users


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_evaluation_message")
@patch("apps.evaluations.models.Evaluator.run")
def test_run_single_evaluation_task_with_bot_generation(
    evaluator_run_mock, handle_evaluation_message_mock, hardcoded_experiment, evaluation_run, evaluation_message
):
    """Test that run_single_evaluation_task calls bot generation before evaluation"""
    from apps.chat.models import ChatMessage

    run, evaluator = evaluation_run

    # Mock responses
    bot_response = ChatMessage(content="Bot generated response")
    handle_evaluation_message_mock.return_value = bot_response
    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"score": 0.8}))

    # Run the evaluation task
    run_single_evaluation_task(run.id, evaluator.id, evaluation_message.id)

    # Verify bot generation was called
    handle_evaluation_message_mock.assert_called_once()

    # Verify evaluator was called
    evaluator_run_mock.assert_called_once_with(evaluation_message)

    # Verify result was created
    result = EvaluationResult.objects.get(message=evaluation_message, run=run, evaluator=evaluator)
    assert result.output == {"score": 0.8}


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_evaluation_message")
@patch("apps.evaluations.models.Evaluator.run")
def test_run_single_evaluation_task_handles_bot_generation_error(
    evaluator_run_mock, handle_evaluation_message_mock, hardcoded_experiment, evaluation_run, evaluation_message
):
    """Test that evaluation continues even if bot generation fails"""
    run, evaluator = evaluation_run

    # Mock bot generation failure
    handle_evaluation_message_mock.side_effect = Exception("Bot generation failed")
    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"score": 0.8}))

    # Run the evaluation task - should not fail
    run_single_evaluation_task(run.id, evaluator.id, evaluation_message.id)

    # Verify evaluator was still called despite bot error
    evaluator_run_mock.assert_called_once_with(evaluation_message)

    # Verify result was still created
    result = EvaluationResult.objects.get(message=evaluation_message, run=run, evaluator=evaluator)
    assert result.output == {"score": 0.8}


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_evaluation_message")
def test_run_bot_generation_missing_hardcoded_experiment(
    handle_evaluation_message_mock, evaluation_message, team_with_users
):
    """Test that _run_bot_generation handles missing hardcoded experiment gracefully"""
    # Don't create the hardcoded experiment

    # Should not raise an exception
    _run_bot_generation(team_with_users, evaluation_message)

    # Bot should not have been called
    handle_evaluation_message_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.channels.tasks.handle_evaluation_message")
def test_run_bot_generation_creates_evaluations_participant(
    handle_evaluation_message_mock, hardcoded_experiment, evaluation_message, team_with_users
):
    """Test that _run_bot_generation creates the evaluations participant if it doesn't exist"""
    from apps.chat.models import ChatMessage

    handle_evaluation_message_mock.return_value = ChatMessage(content="Bot response")

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
@patch("apps.channels.tasks.handle_evaluation_message")
def test_run_bot_generation_creates_session_like_api(
    handle_evaluation_message_mock, hardcoded_experiment, evaluation_message, team_with_users
):
    """Test that _run_bot_generation creates a session similar to the API pattern"""
    from apps.chat.models import ChatMessage

    handle_evaluation_message_mock.return_value = ChatMessage(content="Bot response")

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

    # Verify the session was passed to handle_evaluation_message
    handle_evaluation_message_mock.assert_called_once()
    args, kwargs = handle_evaluation_message_mock.call_args
    assert kwargs["session"] == session
