from unittest.mock import MagicMock, patch

import pytest
from django.http import Http404

from apps.channels.channels_v2.api_channel import NoOpSender
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.stages.core import (
    ConsentFlowStage,
    SessionResolutionStage,
)
from apps.channels.channels_v2.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.channels.channels_v2.web_channel import WebChannel
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException
from apps.experiments.models import Experiment


class TestWebChannelInit:
    def test_requires_existing_session(self):
        with pytest.raises(ChannelException, match="WebChannel requires an existing session"):
            WebChannel(
                experiment=MagicMock(),
                experiment_channel=MagicMock(),
            )

    def test_accepts_session(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        assert channel.experiment_session is not None


class TestWebChannelPipeline:
    def _make_channel(self):
        return WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )

    def test_pipeline_omits_session_resolution_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.core_stages + pipeline.terminal_stages]
        assert SessionResolutionStage not in stage_types

    def test_pipeline_omits_consent_flow_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.core_stages + pipeline.terminal_stages]
        assert ConsentFlowStage not in stage_types

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


class TestWebChannelSender:
    def test_get_sender_returns_no_op_sender(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        sender = channel._get_sender()
        assert isinstance(sender, NoOpSender)


class TestWebChannelCallbacks:
    def test_get_callbacks_returns_base_callbacks(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        callbacks = channel._get_callbacks()
        assert isinstance(callbacks, ChannelCallbacks)


class TestWebChannelCapabilities:
    def test_capabilities_text_only_no_consent(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        caps = channel._get_capabilities()
        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is False
        assert caps.supports_files is False
        assert caps.supports_conversational_consent is False
        assert caps.supported_message_types == [MESSAGE_TYPES.TEXT]


_START_SESSION = "apps.channels.channels_v2.web_channel._start_experiment_session"
_CHECK_SEED = "apps.channels.channels_v2.web_channel.WebChannel.check_and_process_seed_message"
_GET_WEB_CHANNEL = "apps.channels.models.ExperimentChannelObjectManager.get_team_web_channel"


class TestWebChannelStartNewSession:
    @patch(_GET_WEB_CHANNEL, return_value=MagicMock())
    @patch(_START_SESSION)
    @patch(_CHECK_SEED)
    def test_start_new_session_creates_session(self, mock_check_seed, mock_start, mock_get_channel):
        mock_session = MagicMock()
        mock_start.return_value = mock_session
        mock_experiment = MagicMock()
        mock_experiment.get_version.return_value = MagicMock()

        result = WebChannel.start_new_session(
            working_experiment=mock_experiment,
            participant_identifier="user@example.com",
        )

        mock_start.assert_called_once()
        assert result == mock_session

    @patch(_GET_WEB_CHANNEL)
    @patch(_START_SESSION)
    @patch(_CHECK_SEED)
    def test_start_new_session_gets_web_channel(self, mock_check_seed, mock_start, mock_get_channel):
        mock_channel = MagicMock()
        mock_get_channel.return_value = mock_channel
        mock_session = MagicMock()
        mock_start.return_value = mock_session
        mock_experiment = MagicMock()
        mock_experiment.get_version.return_value = MagicMock()

        WebChannel.start_new_session(
            working_experiment=mock_experiment,
            participant_identifier="user@example.com",
        )

        mock_get_channel.assert_called_once_with(mock_experiment.team)
        # Verify the web channel was passed to _start_experiment_session
        call_args = mock_start.call_args
        assert call_args[0][1] == mock_channel  # second positional arg is experiment_channel

    @patch(_GET_WEB_CHANNEL, return_value=MagicMock())
    @patch(_START_SESSION)
    @patch(_CHECK_SEED)
    def test_start_new_session_with_version_sets_metadata(self, mock_check_seed, mock_start, mock_get_channel):
        mock_session = MagicMock()
        mock_start.return_value = mock_session
        mock_experiment = MagicMock()
        mock_experiment.get_version.return_value = MagicMock()

        WebChannel.start_new_session(
            working_experiment=mock_experiment,
            participant_identifier="user@example.com",
            version=42,
        )

        mock_session.chat.set_metadata.assert_called_once()

    @patch(_GET_WEB_CHANNEL, return_value=MagicMock())
    @patch(_START_SESSION)
    @patch(_CHECK_SEED)
    def test_start_new_session_default_version_sets_metadata(self, mock_check_seed, mock_start, mock_get_channel):
        mock_session = MagicMock()
        mock_start.return_value = mock_session
        mock_experiment = MagicMock()
        mock_experiment.get_version.return_value = MagicMock()

        WebChannel.start_new_session(
            working_experiment=mock_experiment,
            participant_identifier="user@example.com",
            version=Experiment.DEFAULT_VERSION_NUMBER,
        )

        # Default version still sets metadata
        mock_session.chat.set_metadata.assert_called_once()

    @patch(_GET_WEB_CHANNEL, return_value=MagicMock())
    @patch(_START_SESSION)
    def test_start_new_session_invalid_version_raises_404(self, mock_start, mock_get_channel):
        mock_session = MagicMock()
        mock_start.return_value = mock_session
        mock_experiment = MagicMock()
        mock_experiment.get_version.side_effect = Experiment.DoesNotExist

        with pytest.raises(Http404):
            WebChannel.start_new_session(
                working_experiment=mock_experiment,
                participant_identifier="user@example.com",
                version=999,
            )

    @patch(_GET_WEB_CHANNEL, return_value=MagicMock())
    @patch(_START_SESSION)
    @patch(_CHECK_SEED)
    def test_start_new_session_calls_check_and_process_seed_message(
        self, mock_check_seed, mock_start, mock_get_channel
    ):
        mock_session = MagicMock()
        mock_start.return_value = mock_session
        mock_experiment = MagicMock()
        experiment_version = MagicMock()
        mock_experiment.get_version.return_value = experiment_version

        WebChannel.start_new_session(
            working_experiment=mock_experiment,
            participant_identifier="user@example.com",
        )

        mock_check_seed.assert_called_once_with(mock_session, experiment_version)

    @patch(_GET_WEB_CHANNEL, return_value=MagicMock())
    @patch(_START_SESSION)
    @patch(_CHECK_SEED)
    def test_start_new_session_passes_metadata(self, mock_check_seed, mock_start, mock_get_channel):
        mock_session = MagicMock()
        mock_start.return_value = mock_session
        mock_experiment = MagicMock()
        mock_experiment.get_version.return_value = MagicMock()

        metadata = {"source": "embed"}
        WebChannel.start_new_session(
            working_experiment=mock_experiment,
            participant_identifier="user@example.com",
            metadata=metadata,
        )

        call_kwargs = mock_start.call_args
        # metadata should be passed through to _start_experiment_session
        assert "metadata" in str(call_kwargs)


class TestWebChannelCheckAndProcessSeedMessage:
    @patch("apps.experiments.tasks.get_response_for_webchat_task")
    def test_with_seed_message_dispatches_task(self, mock_task):
        mock_delay = MagicMock()
        mock_delay.task_id = "task-123"
        mock_task.delay.return_value = mock_delay

        session = MagicMock()
        experiment = MagicMock()
        experiment.seed_message = "Tell a joke"
        experiment.id = 42

        result = WebChannel.check_and_process_seed_message(session, experiment)

        mock_task.delay.assert_called_once_with(
            experiment_session_id=session.id,
            experiment_id=experiment.id,
            message_text="Tell a joke",
            attachments=[],
        )
        assert session.seed_task_id == "task-123"
        session.save.assert_called_once()
        assert result == session

    def test_without_seed_message_no_task(self):
        session = MagicMock()
        experiment = MagicMock()
        experiment.seed_message = ""

        result = WebChannel.check_and_process_seed_message(session, experiment)

        session.save.assert_not_called()
        assert result == session

    def test_without_seed_message_none(self):
        session = MagicMock()
        experiment = MagicMock()
        experiment.seed_message = None

        result = WebChannel.check_and_process_seed_message(session, experiment)

        session.save.assert_not_called()
        assert result == session
