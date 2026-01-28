from unittest.mock import patch

import pytest
from django.db import IntegrityError

from apps.channels.datamodels import BaseMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_evaluation_message
from apps.chat.channels import MESSAGE_TYPES, EvaluationChannel
from apps.chat.models import ChatMessage
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.fixture()
def evaluation_channel(experiment):
    """Create an evaluation channel for the experiment's team"""
    channel = ExperimentChannel.objects.get_team_evaluations_channel(experiment.team)
    return channel


@pytest.mark.django_db()
def test_evaluation_channel_initialization_with_session(experiment, evaluation_channel):
    """Test EvaluationChannel can be initialized with a session"""
    session = ExperimentSessionFactory(experiment=experiment, experiment_channel=evaluation_channel)

    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=evaluation_channel,
        experiment_session=session,
        participant_data={},
    )

    assert channel.experiment_session == session
    assert channel.participant_user == session.participant.user
    assert channel.voice_replies_supported is False

    assert channel.supported_message_types == [MESSAGE_TYPES.TEXT]


@pytest.mark.django_db()
def test_evaluation_channel_disables_ocs_tracer(experiment, evaluation_channel):
    """Test that EvaluationChannel uses empty tracing service (no OCS tracer)"""
    session = ExperimentSessionFactory(experiment=experiment, experiment_channel=evaluation_channel)

    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=evaluation_channel,
        experiment_session=session,
        participant_data={},
    )

    # Verify that the tracing service has no tracers
    assert len(channel.trace_service._tracers) == 0


@pytest.mark.django_db()
def test_get_team_evaluations_channel_uniqueness(experiment):
    """Test that only one evaluation channel per team can be created"""
    team = experiment.team

    # Create first channel
    channel1 = ExperimentChannel.objects.get_team_evaluations_channel(team)

    # Get the same channel again
    channel2 = ExperimentChannel.objects.get_team_evaluations_channel(team)
    assert channel1.id == channel2.id

    # Try to create a second channel manually - should fail due to unique constraint
    with pytest.raises(IntegrityError):
        ExperimentChannel.objects.create(
            team=team, platform=ChannelPlatform.EVALUATIONS, name="another-evaluations-channel"
        )


@pytest.mark.django_db()
@patch("apps.chat.bots.PipelineBot.process_input")
def test_evaluation_channel_processes_message(get_bot_response_mock, experiment, evaluation_channel):
    """Test that EvaluationChannel can process messages"""
    session = ExperimentSessionFactory(experiment=experiment, experiment_channel=evaluation_channel)

    get_bot_response_mock.return_value = ChatMessage(content="Bot response")

    user = experiment.team.members.first()
    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=evaluation_channel,
        experiment_session=session,
        participant_data={},
    )

    message = BaseMessage(participant_id=user.email, message_text="Test message")

    # Should be able to process the message without errors
    result = channel.new_user_message(message)
    assert isinstance(result, ChatMessage)
    assert result.content == "Bot response"


@pytest.mark.django_db()
@patch("apps.chat.bots.PipelineBot.process_input")
def test_handle_evaluation_message(get_bot_response_mock, experiment, evaluation_channel):
    get_bot_response_mock.return_value = ChatMessage(content="Bot response")
    session = ExperimentSessionFactory(experiment=experiment, experiment_channel=evaluation_channel)

    result = handle_evaluation_message(
        experiment_version=experiment,
        experiment_channel=evaluation_channel,
        message_text="Test evaluation message",
        session=session,
        participant_data={},
    )

    assert isinstance(result, ChatMessage)
    assert result.content == "Bot response"


def test_evaluation_channel_participant_data():
    test_state = {"test": "demo"}
    test_pd = {"userid": "1234"}
    experiment = ExperimentFactory.build()
    channel = ExperimentChannelFactory.build(platform=ChannelPlatform.EVALUATIONS)
    session = ExperimentSessionFactory.build(experiment=experiment, experiment_channel=channel, state=test_state)

    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=channel,
        experiment_session=session,
        participant_data=test_pd,
    )
    bot = channel.bot
    state = bot._get_input_state([], "hi")
    assert state["session_state"] == test_state
    assert state["participant_data"] == test_pd
