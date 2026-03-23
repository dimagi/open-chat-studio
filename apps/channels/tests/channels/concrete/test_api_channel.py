from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.api_channel import ApiChannel, NoOpSender
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException
from apps.experiments.models import Experiment


class TestApiChannelInit:
    def test_requires_user_or_session(self):
        with pytest.raises(ChannelException, match="requires either an existing session or a user"):
            ApiChannel(
                experiment=MagicMock(),
                experiment_channel=MagicMock(),
            )

    def test_accepts_user_without_session(self):
        channel = ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            user=MagicMock(),
        )
        assert channel.user is not None

    def test_accepts_session_without_user(self):
        channel = ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        assert channel.experiment_session is not None


class TestApiChannelPipeline:
    def _make_channel(self):
        return ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            user=MagicMock(),
        )

    def test_pipeline_omits_response_sending_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.core_stages + pipeline.terminal_stages]
        assert ResponseSendingStage not in stage_types

    def test_pipeline_omits_sending_error_handler_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.core_stages + pipeline.terminal_stages]
        assert SendingErrorHandlerStage not in stage_types

    def test_pipeline_includes_persistence_and_activity_tracking(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.terminal_stages]
        assert PersistenceStage in stage_types
        assert ActivityTrackingStage in stage_types


class TestApiChannelSender:
    def test_get_sender_returns_no_op_sender(self):
        channel = ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            user=MagicMock(),
        )
        sender = channel._get_sender()
        assert isinstance(sender, NoOpSender)

    def test_no_op_sender_send_text_is_noop(self):
        sender = NoOpSender()
        sender.send_text("hello", "recipient")  # should not raise

    def test_no_op_sender_send_voice_is_noop(self):
        sender = NoOpSender()
        sender.send_voice(MagicMock(), "recipient")  # should not raise

    def test_no_op_sender_send_file_is_noop(self):
        sender = NoOpSender()
        sender.send_file(MagicMock(), "recipient", 1)  # should not raise


class TestApiChannelCallbacks:
    def test_get_callbacks_returns_base_callbacks(self):
        channel = ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            user=MagicMock(),
        )
        callbacks = channel._get_callbacks()
        assert isinstance(callbacks, ChannelCallbacks)


class TestApiChannelCapabilities:
    def test_capabilities_text_only(self):
        channel = ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            user=MagicMock(),
        )
        caps = channel._get_capabilities()
        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice is False
        assert caps.supports_files is False
        assert caps.supported_message_types == [MESSAGE_TYPES.TEXT]
        assert caps.supports_static_triggers is True
        assert caps.supports_conversational_consent is True


class TestApiChannelParticipantUser:
    def test_participant_user_from_session(self):
        mock_session = MagicMock()
        mock_session.participant.user = MagicMock(name="session_user")
        channel = ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=mock_session,
        )
        assert channel.participant_user == mock_session.participant.user

    def test_participant_user_falls_back_to_user(self):
        mock_user = MagicMock(name="fallback_user")
        channel = ApiChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            user=mock_user,
        )
        assert channel.participant_user == mock_user


@pytest.mark.django_db()
class TestApiChannelStartNewSession:
    @patch("apps.channels.channels_v2.api_channel._start_experiment_session")
    def test_start_new_session_delegates(self, mock_start):
        mock_session = MagicMock()
        mock_session.chat = MagicMock()
        mock_start.return_value = mock_session

        experiment = MagicMock()
        channel = MagicMock()

        result = ApiChannel.start_new_session(
            working_experiment=experiment,
            experiment_channel=channel,
            participant_identifier="user@example.com",
        )

        mock_start.assert_called_once()
        assert result == mock_session

    @patch("apps.channels.channels_v2.api_channel._start_experiment_session")
    def test_start_new_session_with_version_sets_metadata(self, mock_start):
        mock_session = MagicMock()
        mock_start.return_value = mock_session

        ApiChannel.start_new_session(
            working_experiment=MagicMock(),
            experiment_channel=MagicMock(),
            participant_identifier="user@example.com",
            version=42,
        )

        mock_session.chat.set_metadata.assert_called_once()

    @patch("apps.channels.channels_v2.api_channel._start_experiment_session")
    def test_start_new_session_default_version_no_metadata(self, mock_start):
        mock_session = MagicMock()
        mock_start.return_value = mock_session

        ApiChannel.start_new_session(
            working_experiment=MagicMock(),
            experiment_channel=MagicMock(),
            participant_identifier="user@example.com",
            version=Experiment.DEFAULT_VERSION_NUMBER,
        )

        mock_session.chat.set_metadata.assert_not_called()
