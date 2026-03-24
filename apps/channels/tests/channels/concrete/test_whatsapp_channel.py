from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

import pytest

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
from apps.channels.channels_v2.whatsapp_channel import (
    WhatsappCallbacks,
    WhatsappChannel,
    WhatsappSender,
)
from apps.chat.channels import MESSAGE_TYPES


@pytest.fixture()
def mock_messaging_service():
    service = MagicMock()
    service.voice_replies_supported = True
    service.supports_multimedia = True
    service.supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    return service


@pytest.fixture()
def whatsapp_channel(mock_messaging_service):
    mock_channel = MagicMock()
    mock_channel.extra_data = {"number": "+15551234567"}
    mock_channel.messaging_provider.type = "twilio"
    mock_channel.messaging_provider.get_messaging_service.return_value = mock_messaging_service
    return WhatsappChannel(experiment=MagicMock(), experiment_channel=mock_channel)


@pytest.fixture()
def meta_cloud_channel(mock_messaging_service):
    mock_channel = MagicMock()
    mock_channel.extra_data = {"phone_number_id": "12345678"}
    mock_channel.messaging_provider.get_messaging_service.return_value = mock_messaging_service
    # Use a string that matches MessagingProviderType.meta_cloud_api
    mock_channel.messaging_provider.type = "meta_cloud_api"
    return WhatsappChannel(experiment=MagicMock(), experiment_channel=mock_channel)


class TestWhatsappChannelInit:
    def test_creates_channel(self, whatsapp_channel):
        assert whatsapp_channel is not None

    def test_messaging_service_lazy_resolved(self, whatsapp_channel):
        # Access messaging_service to trigger lazy resolution
        _ = whatsapp_channel.messaging_service
        whatsapp_channel.experiment_channel.messaging_provider.get_messaging_service.assert_called_once()


class TestWhatsappFromIdentifier:
    def test_twilio_uses_number(self, whatsapp_channel):
        assert whatsapp_channel.from_identifier == "+15551234567"

    @patch("apps.channels.channels_v2.whatsapp_channel.MessagingProviderType", create=True)
    def test_meta_cloud_api_uses_phone_number_id(self, mock_provider_type):
        mock_channel = MagicMock()
        mock_channel.extra_data = {"phone_number_id": "99999"}
        mock_channel.messaging_provider.type = "meta_cloud_api"
        mock_channel.messaging_provider.get_messaging_service.return_value = MagicMock()

        channel = WhatsappChannel(experiment=MagicMock(), experiment_channel=mock_channel)

        # Patch MessagingProviderType.meta_cloud_api to match the mock type
        mock_provider_type.meta_cloud_api = "meta_cloud_api"

        assert channel.from_identifier == "99999"

    @patch("apps.channels.channels_v2.whatsapp_channel.MessagingProviderType", create=True)
    def test_meta_cloud_api_missing_phone_number_id_raises(self, mock_provider_type):
        mock_channel = MagicMock()
        mock_channel.extra_data = {}
        mock_channel.messaging_provider.type = "meta_cloud_api"
        mock_channel.messaging_provider.get_messaging_service.return_value = MagicMock()

        channel = WhatsappChannel(experiment=MagicMock(), experiment_channel=mock_channel)

        mock_provider_type.meta_cloud_api = "meta_cloud_api"

        with pytest.raises(ValueError, match="missing phone_number_id"):
            _ = channel.from_identifier


class TestWhatsappChannelPipeline:
    def test_pipeline_has_all_stages(self, whatsapp_channel):
        pipeline = whatsapp_channel._build_pipeline()

        core_types = [type(s) for s in pipeline.core_stages]
        terminal_types = [type(s) for s in pipeline.terminal_stages]

        assert BotInteractionStage in core_types
        assert ResponseFormattingStage in core_types
        assert ResponseSendingStage in terminal_types
        assert SendingErrorHandlerStage in terminal_types
        assert PersistenceStage in terminal_types
        assert ActivityTrackingStage in terminal_types


class TestWhatsappChannelCapabilities:
    def test_capabilities(self, whatsapp_channel):
        caps = whatsapp_channel._get_capabilities()

        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is True
        assert caps.supports_files is True
        assert caps.supports_conversational_consent is True
        assert caps.supports_static_triggers is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types
        assert MESSAGE_TYPES.VOICE in caps.supported_message_types

    def test_capabilities_no_voice(self, mock_messaging_service):
        mock_messaging_service.voice_replies_supported = False
        mock_messaging_service.supports_multimedia = False

        mock_channel = MagicMock()
        mock_channel.extra_data = {"number": "+15551234567"}
        mock_channel.messaging_provider.type = "twilio"
        mock_channel.messaging_provider.get_messaging_service.return_value = mock_messaging_service

        channel = WhatsappChannel(experiment=MagicMock(), experiment_channel=mock_channel)
        caps = channel._get_capabilities()

        assert caps.supports_voice_replies is False
        assert caps.supports_files is False


class TestWhatsappCanSendFile:
    def test_delegates_to_messaging_service(self, whatsapp_channel, mock_messaging_service):
        mock_file = Mock()
        mock_messaging_service.can_send_file.return_value = True
        assert whatsapp_channel._can_send_file(mock_file) is True
        mock_messaging_service.can_send_file.assert_called_once_with(mock_file)

    def test_returns_false_when_service_has_no_can_send_file(self):
        mock_service = MagicMock(spec=[])  # No attributes
        mock_channel = MagicMock()
        mock_channel.extra_data = {"number": "+15551234567"}
        mock_channel.messaging_provider.type = "twilio"
        mock_channel.messaging_provider.get_messaging_service.return_value = mock_service

        channel = WhatsappChannel(experiment=MagicMock(), experiment_channel=mock_channel)
        assert channel._can_send_file(Mock()) is False


class TestWhatsappSender:
    def test_send_text(self, mock_messaging_service):
        sender = WhatsappSender(mock_messaging_service, "+15551234567", "whatsapp")
        sender.send_text("hello", "+15559876543")
        mock_messaging_service.send_text_message.assert_called_once_with(
            message="hello", from_="+15551234567", to="+15559876543", platform="whatsapp"
        )

    def test_send_voice(self, mock_messaging_service):
        sender = WhatsappSender(mock_messaging_service, "+15551234567", "whatsapp")
        mock_audio = MagicMock()
        sender.send_voice(mock_audio, "+15559876543")
        mock_messaging_service.send_voice_message.assert_called_once_with(
            mock_audio, from_="+15551234567", to="+15559876543", platform="whatsapp"
        )

    def test_send_file(self, mock_messaging_service):
        sender = WhatsappSender(mock_messaging_service, "+15551234567", "whatsapp")
        mock_file = MagicMock()
        mock_file.download_link.return_value = "http://example.com/file.pdf"
        sender.send_file(mock_file, "+15559876543", session_id=42)
        mock_messaging_service.send_file_to_user.assert_called_once_with(
            from_="+15551234567",
            to="+15559876543",
            platform="whatsapp",
            file=mock_file,
            download_link="http://example.com/file.pdf",
        )
        mock_file.download_link.assert_called_once_with(experiment_session_id=42)


class TestWhatsappCallbacks:
    def test_echo_transcript(self, mock_messaging_service):
        sender = WhatsappSender(mock_messaging_service, "+15551234567", "whatsapp")
        callbacks = WhatsappCallbacks(sender=sender, messaging_service=mock_messaging_service)

        callbacks.echo_transcript("+15559876543", "hello world")
        mock_messaging_service.send_text_message.assert_called_once_with(
            message='I heard: "hello world"', from_="+15551234567", to="+15559876543", platform="whatsapp"
        )

    def test_get_message_audio(self, mock_messaging_service):
        mock_messaging_service.get_message_audio.return_value = BytesIO(b"wav_data")
        sender = WhatsappSender(mock_messaging_service, "+15551234567", "whatsapp")
        callbacks = WhatsappCallbacks(sender=sender, messaging_service=mock_messaging_service)

        message = MagicMock()
        result = callbacks.get_message_audio(message)

        mock_messaging_service.get_message_audio.assert_called_once_with(message=message)
        assert result is not None
