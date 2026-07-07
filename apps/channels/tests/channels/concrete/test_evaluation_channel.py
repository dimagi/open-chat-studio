from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError

from apps.channels.channels_v2.evaluation_channel import EvaluationChannel
from apps.channels.datamodels import BaseMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_evaluation_message
from apps.chat.exceptions import ChannelException
from apps.chat.models import ChatMessage
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


def test_requires_existing_session():
    with pytest.raises(ChannelException, match="requires an existing session"):
        EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=None,
            participant_data={},
        )


@pytest.fixture()
def evals_experiment(db):
    return ExperimentFactory.create(team=TeamWithUsersFactory.create())


@pytest.fixture()
def evals_channel(evals_experiment):
    return ExperimentChannel.objects.get_team_evaluations_channel(evals_experiment.team)


@pytest.mark.django_db()
def test_get_team_evaluations_channel_is_idempotent(evals_experiment):
    """Repeated calls return the same team channel instead of creating duplicates"""
    channel1 = ExperimentChannel.objects.get_team_evaluations_channel(evals_experiment.team)
    channel2 = ExperimentChannel.objects.get_team_evaluations_channel(evals_experiment.team)
    assert channel1.id == channel2.id


@pytest.mark.django_db()
def test_team_evaluations_channel_is_unique_per_team(evals_experiment):
    """The DB constraint itself rejects a second evaluations channel for the same team"""
    team = evals_experiment.team
    ExperimentChannel.objects.get_team_evaluations_channel(team)

    with pytest.raises(IntegrityError):
        ExperimentChannel.objects.create(team=team, platform=ChannelPlatform.EVALUATIONS, name="another-evals-channel")


@pytest.mark.django_db()
class TestEvaluationChannelEndToEnd:
    @patch("apps.channels.stages.core.EvalsBot")
    def test_processes_message_with_evals_bot(self, mock_evals_bot_cls, evals_experiment, evals_channel):
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = ChatMessage(content="Bot response")
        mock_evals_bot_cls.return_value = mock_bot
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)
        participant_data = {"userid": "1234"}

        channel = EvaluationChannel(
            experiment=evals_experiment,
            experiment_channel=evals_channel,
            experiment_session=session,
            participant_data=participant_data,
        )
        message = BaseMessage(participant_id=session.participant.identifier, message_text="Hi")

        result = channel.new_user_message(message)

        assert isinstance(result, ChatMessage)
        assert result.content == "Bot response"
        mock_evals_bot_cls.assert_called_once()
        assert mock_evals_bot_cls.call_args.kwargs["participant_data"] == participant_data

    @patch("apps.channels.stages.core.enqueue_static_triggers")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_static_triggers_suppressed(self, mock_process, mock_triggers, evals_experiment, evals_channel):
        mock_process.return_value = ChatMessage(content="Bot response")
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)

        channel = EvaluationChannel(
            experiment=evals_experiment,
            experiment_channel=evals_channel,
            experiment_session=session,
            participant_data={},
        )
        channel.new_user_message(BaseMessage(participant_id=session.participant.identifier, message_text="Hi"))

        mock_triggers.delay.assert_not_called()

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_handle_evaluation_message_task(self, mock_process, evals_experiment, evals_channel):
        mock_process.return_value = ChatMessage(content="Bot response")
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)

        result = handle_evaluation_message(
            experiment_version=evals_experiment,
            experiment_channel=evals_channel,
            message_text="Test evaluation message",
            session=session,
            participant_data={},
        )

        assert isinstance(result, ChatMessage)
        assert result.content == "Bot response"
