from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.slack_channel import (
    SlackChannel,
    SlackSender,
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
from apps.chat.exceptions import ChannelException


@pytest.fixture()
def mock_session():
    session = MagicMock()
    session.external_id = "C12345:1234567890.123456"
    return session


@pytest.fixture()
def mock_messaging_service():
    return MagicMock()


@pytest.fixture()
def slack_channel(mock_session, mock_messaging_service):
    return SlackChannel(
        experiment=MagicMock(),
        experiment_channel=MagicMock(),
        experiment_session=mock_session,
        messaging_service=mock_messaging_service,
    )


class TestSlackChannelInit:
    def test_requires_session(self):
        with pytest.raises(ChannelException, match="requires an existing session"):
            SlackChannel(
                experiment=MagicMock(),
                experiment_channel=MagicMock(),
                experiment_session=None,
            )

    def test_accepts_session(self, mock_session):
        channel = SlackChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=mock_session,
        )
        assert channel.experiment_session is mock_session

    def test_accepts_messaging_service(self, mock_session, mock_messaging_service):
        channel = SlackChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=mock_session,
            messaging_service=mock_messaging_service,
        )
        assert channel.messaging_service is mock_messaging_service

    def test_lazy_messaging_service(self, mock_session):
        mock_channel = MagicMock()
        mock_service = MagicMock()
        mock_channel.messaging_provider.get_messaging_service.return_value = mock_service

        channel = SlackChannel(
            experiment=MagicMock(),
            experiment_channel=mock_channel,
            experiment_session=mock_session,
        )
        assert channel.messaging_service is mock_service
        mock_channel.messaging_provider.get_messaging_service.assert_called_once()


class TestSlackChannelPipeline:
    def test_pipeline_has_all_stages(self, slack_channel):
        pipeline = slack_channel._build_pipeline()

        core_types = [type(s) for s in pipeline.core_stages]
        terminal_types = [type(s) for s in pipeline.terminal_stages]

        assert BotInteractionStage in core_types
        assert ResponseFormattingStage in core_types
        assert ResponseSendingStage in terminal_types
        assert SendingErrorHandlerStage in terminal_types
        assert PersistenceStage in terminal_types
        assert ActivityTrackingStage in terminal_types


class TestSlackChannelCapabilities:
    def test_capabilities(self, slack_channel):
        caps = slack_channel._get_capabilities()

        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is False
        assert caps.supports_files is True
        assert caps.supports_conversational_consent is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types

    def test_no_voice_support(self, slack_channel):
        caps = slack_channel._get_capabilities()
        assert caps.supports_voice_replies is False


class TestSlackChannelCallbacks:
    def test_get_callbacks_returns_base_callbacks(self, slack_channel):
        assert isinstance(slack_channel._get_callbacks(), ChannelCallbacks)


class TestSlackChannelCanSendFile:
    def test_image_within_limit(self, slack_channel):
        file = Mock(content_type="image/jpeg", content_size=5 * 1024 * 1024)
        assert slack_channel._can_send_file(file) is True

    def test_video_within_limit(self, slack_channel):
        file = Mock(content_type="video/mp4", content_size=30 * 1024 * 1024)
        assert slack_channel._can_send_file(file) is True

    def test_audio_within_limit(self, slack_channel):
        file = Mock(content_type="audio/mpeg", content_size=10 * 1024 * 1024)
        assert slack_channel._can_send_file(file) is True

    def test_application_within_limit(self, slack_channel):
        file = Mock(content_type="application/pdf", content_size=5 * 1024 * 1024)
        assert slack_channel._can_send_file(file) is True

    def test_file_exceeding_limit(self, slack_channel):
        file = Mock(content_type="image/jpeg", content_size=60 * 1024 * 1024)
        assert slack_channel._can_send_file(file) is False

    def test_unsupported_mime_type(self, slack_channel):
        file = Mock(content_type="text/plain", content_size=100)
        assert slack_channel._can_send_file(file) is False

    def test_none_content_size_treated_as_zero(self, slack_channel):
        file = Mock(content_type="image/jpeg", content_size=None)
        assert slack_channel._can_send_file(file) is True


class TestSlackSender:
    def test_send_text(self):
        mock_service = MagicMock()
        sender = SlackSender(mock_service, "C12345", "1234567890.123456")

        sender.send_text("hello", "user123")

        mock_service.send_text_message.assert_called_once()
        call_kwargs = mock_service.send_text_message.call_args
        assert call_kwargs[0][0] == "hello"
        assert call_kwargs[1]["from_"] == ""
        assert call_kwargs[1]["to"] == "C12345"
        assert call_kwargs[1]["thread_ts"] == "1234567890.123456"

    def test_send_file(self):
        mock_service = MagicMock()
        sender = SlackSender(mock_service, "C12345", "1234567890.123456")
        mock_file = MagicMock()

        sender.send_file(mock_file, "user123", session_id=1)

        mock_service.send_file_message.assert_called_once_with(
            file=mock_file,
            to="C12345",
            thread_ts="1234567890.123456",
        )

    def test_send_voice_raises(self):
        mock_service = MagicMock()
        sender = SlackSender(mock_service, "C12345", "1234567890.123456")

        with pytest.raises(NotImplementedError):
            sender.send_voice(MagicMock(), "user123")

    @patch(
        "apps.slack.utils.parse_session_external_id",
        return_value=("C12345", "1234567890.123456"),
    )
    def test_get_sender_builds_from_session_external_id(self, mock_parse, mock_session, mock_messaging_service):
        channel = SlackChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=mock_session,
            messaging_service=mock_messaging_service,
        )
        sender = channel._get_sender()
        assert isinstance(sender, SlackSender)
        mock_parse.assert_called_once_with(mock_session.external_id)
