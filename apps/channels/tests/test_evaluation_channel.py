from unittest.mock import patch

import pytest
from django.db import IntegrityError

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import EvaluationChannel
from apps.chat.exceptions import ChannelException
from apps.chat.models import ChatMessage
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
def test_evaluation_channel_initialization_with_user(experiment, evaluation_channel):
    """Test EvaluationChannel can be initialized with a user"""
    user = experiment.team.members.first()

    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=evaluation_channel,
        user=user,
    )

    assert channel.user == user
    assert channel.participant_user == user
    assert channel.voice_replies_supported is False
    from apps.chat.channels import MESSAGE_TYPES

    assert channel.supported_message_types == [MESSAGE_TYPES.TEXT]


@pytest.mark.django_db()
def test_evaluation_channel_initialization_with_session(experiment, evaluation_channel):
    """Test EvaluationChannel can be initialized with an existing session"""
    session = ExperimentSessionFactory(experiment=experiment, experiment_channel=evaluation_channel)

    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=evaluation_channel,
        experiment_session=session,
    )

    assert channel.experiment_session == session
    assert channel.participant_user == session.participant.user


@pytest.mark.django_db()
def test_evaluation_channel_initialization_fails_without_user_or_session(experiment, evaluation_channel):
    """Test EvaluationChannel initialization fails without user or session"""
    with pytest.raises(ChannelException, match="EvaluationChannel requires either an existing session or a user"):
        EvaluationChannel(
            experiment=experiment,
            experiment_channel=evaluation_channel,
        )


@pytest.mark.django_db()
def test_evaluation_channel_send_text_to_user_does_nothing(experiment, evaluation_channel):
    """Test that send_text_to_user is a no-op for evaluation channels"""
    user = experiment.team.members.first()
    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=evaluation_channel,
        user=user,
    )

    # Should not raise any exception and should do nothing
    result = channel.send_text_to_user("Test message")
    assert result is None


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
@patch("apps.chat.channels.EvaluationChannel._get_bot_response")
def test_evaluation_channel_processes_message(get_bot_response_mock, experiment, evaluation_channel):
    """Test that EvaluationChannel can process messages like other channels"""
    get_bot_response_mock.return_value = ChatMessage(content="Bot response")

    user = experiment.team.members.first()
    channel = EvaluationChannel(
        experiment=experiment,
        experiment_channel=evaluation_channel,
        user=user,
    )

    from apps.channels.datamodels import BaseMessage

    message = BaseMessage(participant_id=user.email, message_text="Test message")

    # Should be able to process the message without errors
    result = channel.new_user_message(message)
    assert isinstance(result, ChatMessage)
    assert result.content == "Bot response"
