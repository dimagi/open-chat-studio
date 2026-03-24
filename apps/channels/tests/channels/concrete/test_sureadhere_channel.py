from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.stages.core import (
    BotInteractionStage,
    ResponseFormattingStage,
)
from apps.channels.channels_v2.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.channels.channels_v2.sureadhere_channel import (
    SureAdhereChannel,
    SureAdhereSender,
)
from apps.chat.channels import MESSAGE_TYPES


@pytest.fixture()
def mock_messaging_service():
    service = MagicMock()
    service.voice_replies_supported = False
    service.supported_message_types = [MESSAGE_TYPES.TEXT]
    return service


@pytest.fixture()
def sureadhere_channel(mock_messaging_service):
    mock_channel = MagicMock()
    mock_channel.extra_data = {"sureadhere_tenant_id": "tenant_abc"}
    mock_channel.messaging_provider.get_messaging_service.return_value = mock_messaging_service
    return SureAdhereChannel(experiment=MagicMock(), experiment_channel=mock_channel)


class TestSureAdhereChannelInit:
    def test_creates_channel(self, sureadhere_channel):
        assert sureadhere_channel is not None

    def test_messaging_service_lazy_resolved(self, sureadhere_channel):
        _ = sureadhere_channel.messaging_service
        sureadhere_channel.experiment_channel.messaging_provider.get_messaging_service.assert_called_once()

    def test_tenant_id(self, sureadhere_channel):
        assert sureadhere_channel.tenant_id == "tenant_abc"


class TestSureAdhereChannelPipeline:
    def test_pipeline_has_all_stages(self, sureadhere_channel):
        pipeline = sureadhere_channel._build_pipeline()

        core_types = [type(s) for s in pipeline.core_stages]
        terminal_types = [type(s) for s in pipeline.terminal_stages]

        assert BotInteractionStage in core_types
        assert ResponseFormattingStage in core_types
        assert ResponseSendingStage in terminal_types
        assert SendingErrorHandlerStage in terminal_types
        assert PersistenceStage in terminal_types
        assert ActivityTrackingStage in terminal_types


class TestSureAdhereChannelCapabilities:
    def test_capabilities(self, sureadhere_channel):
        caps = sureadhere_channel._get_capabilities()

        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is False
        assert caps.supports_files is False
        assert caps.supports_conversational_consent is True
        assert caps.supports_static_triggers is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types

    def test_no_voice_types(self, sureadhere_channel):
        caps = sureadhere_channel._get_capabilities()
        # SureAdhere is text-only
        assert MESSAGE_TYPES.VOICE not in caps.supported_message_types


class TestSureAdhereChannelCallbacks:
    def test_callbacks_are_base_noop(self, sureadhere_channel):
        callbacks = sureadhere_channel._get_callbacks()
        assert type(callbacks) is ChannelCallbacks


class TestSureAdhereSender:
    def test_send_text(self, mock_messaging_service):
        sender = SureAdhereSender(mock_messaging_service, "tenant_abc", "sureadhere")
        sender.send_text("hello", "patient_123")
        mock_messaging_service.send_text_message.assert_called_once_with(
            message="hello", from_="tenant_abc", to="patient_123", platform="sureadhere"
        )

    def test_send_voice_not_implemented(self, mock_messaging_service):
        sender = SureAdhereSender(mock_messaging_service, "tenant_abc", "sureadhere")
        with pytest.raises(NotImplementedError):
            sender.send_voice(MagicMock(), "patient_123")

    def test_send_file_not_implemented(self, mock_messaging_service):
        sender = SureAdhereSender(mock_messaging_service, "tenant_abc", "sureadhere")
        with pytest.raises(NotImplementedError):
            sender.send_file(MagicMock(), "patient_123", session_id=1)
