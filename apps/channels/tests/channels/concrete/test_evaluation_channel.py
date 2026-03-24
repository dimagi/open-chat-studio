from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.api_channel import NoOpSender
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.evaluation_channel import (
    EvalsBotInteractionStage,
    EvaluationChannel,
)
from apps.channels.channels_v2.stages.core import BotInteractionStage
from apps.channels.channels_v2.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.channels.datamodels import BaseMessage
from apps.channels.models import ExperimentChannel
from apps.channels.tasks import handle_evaluation_message
from apps.channels.tests.channels.conftest import make_context
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException
from apps.chat.models import ChatMessage
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


class TestEvaluationChannelInit:
    def test_requires_session(self):
        with pytest.raises(ChannelException, match="requires an existing session"):
            EvaluationChannel(
                experiment=MagicMock(),
                experiment_channel=MagicMock(),
                experiment_session=None,
                participant_data={},
            )

    def test_accepts_session(self):
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={"key": "value"},
        )
        assert channel.experiment_session is not None

    def test_disables_ocs_tracer(self):
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={},
        )
        assert len(channel.trace_service._tracers) == 0


class TestEvaluationChannelPipeline:
    def _make_channel(self):
        return EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={},
        )

    @pytest.mark.parametrize(
        "stage_class",
        [ResponseSendingStage, SendingErrorHandlerStage],
        ids=["response_sending", "sending_error_handler"],
    )
    def test_pipeline_omits_sending_stages(self, stage_class):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()
        stage_types = [type(s) for s in pipeline.core_stages + pipeline.terminal_stages]
        assert stage_class not in stage_types

    def test_pipeline_omits_bot_interaction_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()
        stage_types = [type(s) for s in pipeline.core_stages]
        assert BotInteractionStage not in stage_types

    def test_pipeline_uses_evals_bot_interaction_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()
        stage_types = [type(s) for s in pipeline.core_stages]
        assert EvalsBotInteractionStage in stage_types

    def test_pipeline_includes_persistence_and_activity_tracking(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()
        stage_types = [type(s) for s in pipeline.terminal_stages]
        assert PersistenceStage in stage_types
        assert ActivityTrackingStage in stage_types


class TestEvaluationChannelSender:
    def test_get_sender_returns_no_op_sender(self):
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={},
        )
        assert isinstance(channel._get_sender(), NoOpSender)


class TestEvaluationChannelCallbacks:
    def test_get_callbacks_returns_base_callbacks(self):
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={},
        )
        assert isinstance(channel._get_callbacks(), ChannelCallbacks)


class TestEvaluationChannelCapabilities:
    def test_capabilities(self):
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={},
        )
        caps = channel._get_capabilities()
        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is False
        assert caps.supports_files is False
        assert caps.supports_conversational_consent is False
        assert caps.supports_static_triggers is False
        assert caps.supported_message_types == [MESSAGE_TYPES.TEXT]


class TestEvaluationChannelContext:
    def test_create_context_includes_participant_data(self):
        pd = {"userid": "1234"}
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data=pd,
        )
        ctx = channel._create_context(MagicMock())
        assert ctx.channel_context["participant_data"] == pd


class TestEvaluationChannelParticipantUser:
    def test_participant_user_from_session(self):
        mock_session = MagicMock()
        mock_session.participant.user = MagicMock(name="session_user")
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=mock_session,
            participant_data={},
        )
        assert channel.participant_user == mock_session.participant.user


class TestEvalsBotInteractionStage:
    def test_should_run_false_when_no_user_query(self):
        stage = EvalsBotInteractionStage()
        ctx = make_context(user_query=None)
        assert stage.should_run(ctx) is False

    def test_should_run_true_when_user_query_set(self):
        stage = EvalsBotInteractionStage()
        ctx = make_context(user_query="hello")
        assert stage.should_run(ctx) is True

    @patch("apps.chat.bots.EvalsBot")
    def test_process_creates_evals_bot(self, mock_evals_bot_cls):
        mock_bot = MagicMock()
        mock_response = MagicMock()
        mock_response.get_attached_files.return_value = []
        mock_bot.process_input.return_value = mock_response
        mock_evals_bot_cls.return_value = mock_bot

        pd = {"userid": "1234"}
        ctx = make_context(
            user_query="test query",
            channel_context={"participant_data": pd},
            experiment_session=MagicMock(),
        )

        stage = EvalsBotInteractionStage()
        stage.process(ctx)

        mock_evals_bot_cls.assert_called_once_with(
            ctx.experiment_session,
            ctx.experiment,
            ctx.trace_service,
            participant_data=pd,
        )
        assert ctx.bot == mock_bot
        assert ctx.bot_response == mock_response

    @patch("apps.chat.bots.EvalsBot")
    def test_process_extracts_files(self, mock_evals_bot_cls):
        mock_files = [MagicMock(), MagicMock()]
        mock_response = MagicMock()
        mock_response.get_attached_files.return_value = mock_files
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = mock_response
        mock_evals_bot_cls.return_value = mock_bot

        ctx = make_context(
            user_query="test",
            channel_context={"participant_data": {}},
            experiment_session=MagicMock(),
        )

        stage = EvalsBotInteractionStage()
        stage.process(ctx)

        assert ctx.files_to_send == mock_files


@pytest.mark.django_db()
class TestEvaluationChannelIntegration:
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_processes_message(self, get_bot_response_mock):
        experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
        evaluation_channel = ExperimentChannel.objects.get_team_evaluations_channel(experiment.team)
        session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=evaluation_channel)

        get_bot_response_mock.return_value = ChatMessage(content="Bot response")

        user = experiment.team.members.first()
        channel = EvaluationChannel(
            experiment=experiment,
            experiment_channel=evaluation_channel,
            experiment_session=session,
            participant_data={},
        )

        message = BaseMessage(participant_id=user.email, message_text="Test message")
        result = channel.new_user_message(message)
        assert isinstance(result, ChatMessage)
        assert result.content == "Bot response"

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_handle_evaluation_message(self, get_bot_response_mock):
        experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
        evaluation_channel = ExperimentChannel.objects.get_team_evaluations_channel(experiment.team)
        session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=evaluation_channel)

        get_bot_response_mock.return_value = ChatMessage(content="Bot response")

        result = handle_evaluation_message(
            experiment_version=experiment,
            experiment_channel=evaluation_channel,
            message_text="Test evaluation message",
            session=session,
            participant_data={},
        )

        assert isinstance(result, ChatMessage)
        assert result.content == "Bot response"
