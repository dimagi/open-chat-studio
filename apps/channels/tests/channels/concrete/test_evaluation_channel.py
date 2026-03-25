from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.evaluation_channel import EvaluationChannel
from apps.channels.channels_v2.stages.core import (
    BotInteractionStage,
    ConsentFlowStage,
    EvalsBotInteractionStage,
    SessionResolutionStage,
)
from apps.channels.channels_v2.stages.terminal import (
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.channels.datamodels import BaseMessage
from apps.channels.models import ExperimentChannel
from apps.chat.exceptions import ChannelException
from apps.chat.models import ChatMessage
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


class TestEvaluationChannelInit:
    def test_requires_existing_session(self):
        with pytest.raises(ChannelException, match="EvaluationChannel requires an existing session"):
            EvaluationChannel(
                experiment=MagicMock(),
                experiment_channel=MagicMock(),
                experiment_session=None,
                participant_data={},
            )

    def test_accepts_session_and_participant_data(self):
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={"key": "value"},
        )
        assert channel.experiment_session is not None
        assert channel._participant_data == {"key": "value"}

    def test_uses_empty_trace_service(self):
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
        [
            SessionResolutionStage,
            ConsentFlowStage,
            ResponseSendingStage,
            SendingErrorHandlerStage,
            BotInteractionStage,
        ],
        ids=[
            "session_resolution",
            "consent_flow",
            "response_sending",
            "sending_error_handler",
            "bot_interaction",
        ],
    )
    def test_pipeline_omits_stage(self, stage_class):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.core_stages + pipeline.terminal_stages]
        assert stage_class not in stage_types

    def test_pipeline_uses_evals_bot_interaction_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.core_stages]
        assert EvalsBotInteractionStage in stage_types


class TestEvaluationChannelCallbacks:
    def test_get_callbacks_returns_base_callbacks(self):
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data={},
        )
        callbacks = channel._get_callbacks()
        assert isinstance(callbacks, ChannelCallbacks)


class TestEvaluationChannelContext:
    def test_create_context_sets_participant_data(self):
        participant_data = {"userid": "1234"}
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
            participant_data=participant_data,
        )
        message = MagicMock()
        message.model_dump.return_value = {}
        ctx = channel._create_context(message)

        assert ctx.channel_context == {"participant_data": participant_data}

    def test_create_context_includes_session(self):
        session = MagicMock()
        channel = EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=session,
            participant_data={},
        )
        message = MagicMock()
        message.model_dump.return_value = {}
        ctx = channel._create_context(message)

        assert ctx.experiment_session == session


class TestEvaluationChannelIntegration:
    @pytest.mark.django_db()
    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_processes_message_end_to_end(self, mock_evals_bot_cls):
        """Integration test: full pipeline processes a text message and returns bot response."""
        team = TeamWithUsersFactory.create()
        experiment = ExperimentFactory.create(team=team)
        evaluation_channel = ExperimentChannel.objects.get_team_evaluations_channel(team)
        session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=evaluation_channel)

        mock_response = ChatMessage(content="Bot response")
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = mock_response
        mock_evals_bot_cls.return_value = mock_bot

        channel = EvaluationChannel(
            experiment=experiment,
            experiment_channel=evaluation_channel,
            experiment_session=session,
            participant_data={"test": "data"},
        )

        message = BaseMessage(participant_id=session.participant.identifier, message_text="Test message")
        result = channel.new_user_message(message)

        assert isinstance(result, ChatMessage)
        assert result.content == "Bot response"
        # Verify EvalsBot was created with correct participant_data
        mock_evals_bot_cls.assert_called_once()
        call_kwargs = mock_evals_bot_cls.call_args
        assert call_kwargs.kwargs["participant_data"] == {"test": "data"}
