from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.commcare_channel import (
    CommCareConnectChannel,
    CommCareConnectSender,
    CommCareConsentCheckStage,
)
from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.channels.channels_v2.stages.core import (
    BotInteractionStage,
    ConsentFlowStage,
    ResponseFormattingStage,
    SessionResolutionStage,
)
from apps.channels.channels_v2.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.channels.tests.channels.conftest import make_context
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException

# Patch targets for local imports
_PARTICIPANT_DATA_PATH = "apps.experiments.models.ParticipantData"
_CONNECT_CLIENT_PATH = "apps.channels.clients.connect_client.CommCareConnectClient"


def _make_channel():
    return CommCareConnectChannel(
        experiment=MagicMock(),
        experiment_channel=MagicMock(),
    )


class TestCommCareConsentCheckStage:
    def test_should_run_false_when_no_session(self):
        stage = CommCareConsentCheckStage()
        ctx = make_context(experiment_session=None)
        assert stage.should_run(ctx) is False

    def test_should_run_true_when_session_exists(self):
        stage = CommCareConsentCheckStage()
        ctx = make_context(experiment_session=MagicMock())
        assert stage.should_run(ctx) is True

    @patch(_PARTICIPANT_DATA_PATH)
    def test_raises_early_exit_when_participant_data_missing(self, mock_pd_cls):
        mock_pd_cls.DoesNotExist = Exception
        mock_pd_cls.objects.get.side_effect = mock_pd_cls.DoesNotExist

        stage = CommCareConsentCheckStage()
        ctx = make_context(
            experiment_session=MagicMock(),
            participant_identifier="user123",
        )

        with pytest.raises(EarlyExitResponse, match="consent"):
            stage.process(ctx)

    @patch(_PARTICIPANT_DATA_PATH)
    def test_raises_early_exit_when_consent_false(self, mock_pd_cls):
        mock_pd = MagicMock()
        mock_pd.system_metadata = {"consent": False}
        mock_pd_cls.objects.get.return_value = mock_pd

        stage = CommCareConsentCheckStage()
        ctx = make_context(
            experiment_session=MagicMock(),
            participant_identifier="user123",
        )

        with pytest.raises(EarlyExitResponse, match="consent"):
            stage.process(ctx)

    @patch(_PARTICIPANT_DATA_PATH)
    def test_passes_when_consent_true(self, mock_pd_cls):
        mock_pd = MagicMock()
        mock_pd.system_metadata = {"consent": True}
        mock_pd_cls.objects.get.return_value = mock_pd

        stage = CommCareConsentCheckStage()
        ctx = make_context(
            experiment_session=MagicMock(),
            participant_identifier="user123",
        )

        # Should not raise
        stage.process(ctx)

    @patch(_PARTICIPANT_DATA_PATH)
    def test_consent_missing_defaults_to_false(self, mock_pd_cls):
        mock_pd = MagicMock()
        mock_pd.system_metadata = {}  # No "consent" key
        mock_pd_cls.objects.get.return_value = mock_pd

        stage = CommCareConsentCheckStage()
        ctx = make_context(
            experiment_session=MagicMock(),
            participant_identifier="user123",
        )

        with pytest.raises(EarlyExitResponse, match="consent"):
            stage.process(ctx)


class TestCommCareConnectSender:
    @patch(_CONNECT_CLIENT_PATH)
    def test_send_text(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_channel = MagicMock(spec=CommCareConnectChannel)
        mock_channel.connect_channel_id = "channel-uuid"
        mock_channel.encryption_key = b"encryption-key"

        sender = CommCareConnectSender(mock_channel)
        sender.send_text("hello", "user123")

        mock_client.send_message_to_user.assert_called_once_with(
            channel_id="channel-uuid",
            message="hello",
            encryption_key=b"encryption-key",
        )

    @patch(_CONNECT_CLIENT_PATH)
    def test_send_voice_raises(self, mock_client_cls):
        mock_channel = MagicMock(spec=CommCareConnectChannel)
        sender = CommCareConnectSender(mock_channel)

        with pytest.raises(NotImplementedError):
            sender.send_voice(MagicMock(), "user123")

    @patch(_CONNECT_CLIENT_PATH)
    def test_send_file_raises(self, mock_client_cls):
        mock_channel = MagicMock(spec=CommCareConnectChannel)
        sender = CommCareConnectSender(mock_channel)

        with pytest.raises(NotImplementedError):
            sender.send_file(MagicMock(), "user123", session_id=1)


class TestCommCareConnectChannelPipeline:
    def test_pipeline_includes_consent_check_stage(self):
        channel = _make_channel()
        pipeline = channel._build_pipeline()
        core_types = [type(s) for s in pipeline.core_stages]
        assert CommCareConsentCheckStage in core_types

    def test_consent_check_after_session_activation(self):
        channel = _make_channel()
        pipeline = channel._build_pipeline()
        core_types = [type(s) for s in pipeline.core_stages]
        consent_idx = core_types.index(CommCareConsentCheckStage)
        # SessionResolutionStage should come before consent check
        assert core_types.index(SessionResolutionStage) < consent_idx

    def test_pipeline_includes_all_standard_stages(self):
        channel = _make_channel()
        pipeline = channel._build_pipeline()
        core_types = [type(s) for s in pipeline.core_stages]
        terminal_types = [type(s) for s in pipeline.terminal_stages]

        assert BotInteractionStage in core_types
        assert ConsentFlowStage in core_types
        assert ResponseFormattingStage in core_types
        assert ResponseSendingStage in terminal_types
        assert SendingErrorHandlerStage in terminal_types
        assert PersistenceStage in terminal_types
        assert ActivityTrackingStage in terminal_types


class TestCommCareConnectChannelCapabilities:
    def test_capabilities(self):
        channel = _make_channel()
        caps = channel._get_capabilities()

        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is False
        assert caps.supports_files is False
        assert caps.supports_conversational_consent is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types


class TestCommCareConnectChannelCallbacks:
    def test_get_callbacks_returns_base_callbacks(self):
        channel = _make_channel()
        assert isinstance(channel._get_callbacks(), ChannelCallbacks)


class TestCommCareConnectChannelSender:
    @patch(_CONNECT_CLIENT_PATH)
    def test_get_sender_returns_commcare_sender(self, mock_client_cls):
        channel = _make_channel()
        sender = channel._get_sender()
        assert isinstance(sender, CommCareConnectSender)


class TestCommCareConnectChannelProperties:
    def test_connect_channel_id_from_participant_data(self):
        channel = CommCareConnectChannel.__new__(CommCareConnectChannel)
        mock_pd = MagicMock()
        mock_pd.system_metadata = {"commcare_connect_channel_id": "test-channel-uuid"}

        with patch.object(type(channel), "participant_data", new_callable=PropertyMock, return_value=mock_pd):
            assert channel.connect_channel_id == "test-channel-uuid"

    def test_connect_channel_id_raises_when_missing(self):
        channel = CommCareConnectChannel.__new__(CommCareConnectChannel)
        channel.experiment_session = MagicMock()
        channel.experiment_session.participant.identifier = "user123"
        mock_pd = MagicMock()
        mock_pd.system_metadata = {}

        with patch.object(type(channel), "participant_data", new_callable=PropertyMock, return_value=mock_pd):
            with pytest.raises(ChannelException, match="channel_id is missing"):
                _ = channel.connect_channel_id

    def test_encryption_key_generates_if_missing(self):
        channel = CommCareConnectChannel.__new__(CommCareConnectChannel)
        mock_pd = MagicMock()
        mock_pd.encryption_key = None
        mock_pd.get_encryption_key_bytes.return_value = b"generated-key"

        with patch.object(type(channel), "participant_data", new_callable=PropertyMock, return_value=mock_pd):
            result = channel.encryption_key
            mock_pd.generate_encryption_key.assert_called_once()
            assert result == b"generated-key"

    def test_encryption_key_uses_existing(self):
        channel = CommCareConnectChannel.__new__(CommCareConnectChannel)
        mock_pd = MagicMock()
        mock_pd.encryption_key = "existing-key"
        mock_pd.get_encryption_key_bytes.return_value = b"existing-key"

        with patch.object(type(channel), "participant_data", new_callable=PropertyMock, return_value=mock_pd):
            result = channel.encryption_key
            mock_pd.generate_encryption_key.assert_not_called()
            assert result == b"existing-key"
