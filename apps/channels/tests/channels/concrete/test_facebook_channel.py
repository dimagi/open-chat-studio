from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.facebook_channel import (
    FacebookCallbacks,
    FacebookMessengerChannel,
    FacebookSender,
)
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
from apps.chat.channels import MESSAGE_TYPES


@pytest.fixture()
def mock_messaging_service():
    service = MagicMock()
    service.voice_replies_supported = True
    service.supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    return service


@pytest.fixture()
def facebook_channel(mock_messaging_service):
    mock_channel = MagicMock()
    mock_channel.extra_data = {"page_id": "page_123"}
    mock_channel.messaging_provider.get_messaging_service.return_value = mock_messaging_service
    return FacebookMessengerChannel(experiment=MagicMock(), experiment_channel=mock_channel)


class TestFacebookMessengerChannelInit:
    def test_creates_channel(self, facebook_channel):
        assert facebook_channel is not None

    def test_messaging_service_lazy_resolved(self, facebook_channel):
        _ = facebook_channel.messaging_service
        facebook_channel.experiment_channel.messaging_provider.get_messaging_service.assert_called_once()

    def test_page_id(self, facebook_channel):
        assert facebook_channel.page_id == "page_123"


class TestFacebookMessengerChannelPipeline:
    def test_pipeline_has_all_stages(self, facebook_channel):
        pipeline = facebook_channel._build_pipeline()

        core_types = [type(s) for s in pipeline.core_stages]
        terminal_types = [type(s) for s in pipeline.terminal_stages]

        assert BotInteractionStage in core_types
        assert ResponseFormattingStage in core_types
        assert ResponseSendingStage in terminal_types
        assert SendingErrorHandlerStage in terminal_types
        assert PersistenceStage in terminal_types
        assert ActivityTrackingStage in terminal_types


class TestFacebookMessengerChannelCapabilities:
    def test_capabilities(self, facebook_channel):
        caps = facebook_channel._get_capabilities()

        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is True
        assert caps.supports_files is False
        assert caps.supports_conversational_consent is True
        assert caps.supports_static_triggers is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types
        assert MESSAGE_TYPES.VOICE in caps.supported_message_types

    def test_capabilities_no_voice(self, mock_messaging_service):
        mock_messaging_service.voice_replies_supported = False

        mock_channel = MagicMock()
        mock_channel.extra_data = {"page_id": "page_123"}
        mock_channel.messaging_provider.get_messaging_service.return_value = mock_messaging_service

        channel = FacebookMessengerChannel(experiment=MagicMock(), experiment_channel=mock_channel)
        caps = channel._get_capabilities()

        assert caps.supports_voice_replies is False


class TestFacebookSender:
    def test_send_text(self, mock_messaging_service):
        sender = FacebookSender(mock_messaging_service, "page_123", "facebook")
        sender.send_text("hello", "user_456")
        mock_messaging_service.send_text_message.assert_called_once_with(
            message="hello", from_="page_123", to="user_456", platform="facebook"
        )

    def test_send_voice(self, mock_messaging_service):
        sender = FacebookSender(mock_messaging_service, "page_123", "facebook")
        mock_audio = MagicMock()
        sender.send_voice(mock_audio, "user_456")
        mock_messaging_service.send_voice_message.assert_called_once_with(
            mock_audio, from_="page_123", to="user_456", platform="facebook"
        )


class TestFacebookCallbacks:
    def test_echo_transcript(self, mock_messaging_service):
        sender = FacebookSender(mock_messaging_service, "page_123", "facebook")
        callbacks = FacebookCallbacks(sender=sender, messaging_service=mock_messaging_service)

        callbacks.echo_transcript("user_456", "hello world")
        mock_messaging_service.send_text_message.assert_called_once_with(
            message='I heard: "hello world"', from_="page_123", to="user_456", platform="facebook"
        )
