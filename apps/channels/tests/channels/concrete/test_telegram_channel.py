from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

import pytest
from telebot.apihelper import ApiTelegramException

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
from apps.channels.channels_v2.telegram_channel import (
    TelegramCallbacks,
    TelegramChannel,
    TelegramSender,
)
from apps.chat.channels import MESSAGE_TYPES


@pytest.fixture()
def mock_telebot():
    """Patch TeleBot at its source (telebot module) so local imports find the mock."""
    with patch("telebot.TeleBot") as mock_cls:
        yield mock_cls


@pytest.fixture()
def telegram_channel(mock_telebot):
    mock_channel = MagicMock()
    mock_channel.extra_data = {"bot_token": "fake_token"}
    return TelegramChannel(experiment=MagicMock(), experiment_channel=mock_channel)


class TestTelegramChannelInit:
    def test_creates_telegram_bot(self, mock_telebot):
        mock_channel = MagicMock()
        mock_channel.extra_data = {"bot_token": "fake_token"}
        channel = TelegramChannel(experiment=MagicMock(), experiment_channel=mock_channel)
        mock_telebot.assert_called_once_with("fake_token", threaded=False)
        assert channel.telegram_bot is not None


class TestTelegramChannelPipeline:
    def test_pipeline_has_all_stages(self, telegram_channel):
        pipeline = telegram_channel._build_pipeline()

        core_types = [type(s) for s in pipeline.core_stages]
        terminal_types = [type(s) for s in pipeline.terminal_stages]

        assert BotInteractionStage in core_types
        assert ResponseFormattingStage in core_types
        assert ResponseSendingStage in terminal_types
        assert SendingErrorHandlerStage in terminal_types
        assert PersistenceStage in terminal_types
        assert ActivityTrackingStage in terminal_types


class TestTelegramChannelCapabilities:
    def test_capabilities(self, telegram_channel):
        caps = telegram_channel._get_capabilities()

        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is True
        assert caps.supports_files is True
        assert caps.supports_conversational_consent is True
        assert caps.supports_static_triggers is True
        assert MESSAGE_TYPES.TEXT in caps.supported_message_types
        assert MESSAGE_TYPES.VOICE in caps.supported_message_types


class TestTelegramChannelCanSendFile:
    def test_small_image_supported(self, telegram_channel):
        file = Mock(content_type="image/jpeg", content_size=5 * 1024 * 1024)
        assert telegram_channel._can_send_file(file) is True

    def test_large_image_not_supported(self, telegram_channel):
        file = Mock(content_type="image/jpeg", content_size=15 * 1024 * 1024)
        assert telegram_channel._can_send_file(file) is False

    def test_small_video_supported(self, telegram_channel):
        file = Mock(content_type="video/mp4", content_size=30 * 1024 * 1024)
        assert telegram_channel._can_send_file(file) is True

    def test_large_video_not_supported(self, telegram_channel):
        file = Mock(content_type="video/mp4", content_size=60 * 1024 * 1024)
        assert telegram_channel._can_send_file(file) is False

    def test_unknown_mime_not_supported(self, telegram_channel):
        file = Mock(content_type="text/plain", content_size=100)
        assert telegram_channel._can_send_file(file) is False

    def test_application_file_supported(self, telegram_channel):
        file = Mock(content_type="application/pdf", content_size=5 * 1024 * 1024)
        assert telegram_channel._can_send_file(file) is True


class TestTelegramSender:
    def test_send_text_single_chunk(self):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)

        with patch("telebot.util.smart_split", return_value=["hello"]):
            with patch("telebot.util.antiflood") as mock_antiflood:
                sender.send_text("hello", "12345")
                mock_antiflood.assert_called_once_with(mock_bot.send_message, "12345", text="hello")

    def test_send_text_multiple_chunks(self):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)

        with patch("telebot.util.smart_split", return_value=["a", "b"]):
            with patch("telebot.util.antiflood") as mock_antiflood:
                sender.send_text("ab", "12345")
                assert mock_antiflood.call_count == 2

    def test_send_voice(self):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)
        mock_audio = MagicMock()
        mock_audio.audio = BytesIO(b"audio_data")
        mock_audio.duration = 5

        with patch("telebot.util.antiflood") as mock_antiflood:
            sender.send_voice(mock_audio, "12345")
            mock_antiflood.assert_called_once_with(
                mock_bot.send_voice,
                "12345",
                voice=mock_audio.audio,
                duration=mock_audio.duration,
            )

    @pytest.mark.parametrize(
        ("content_type", "expected_method"),
        [
            ("image/jpeg", "send_photo"),
            ("video/mp4", "send_video"),
            ("audio/mpeg", "send_audio"),
            ("application/pdf", "send_document"),
        ],
        ids=["image", "video", "audio", "document"],
    )
    def test_send_file_dispatches_by_mime(self, content_type, expected_method):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)
        mock_file = Mock(content_type=content_type, file="file_data")

        with patch("telebot.util.antiflood") as mock_antiflood:
            sender.send_file(mock_file, "12345", session_id=1)
            mock_antiflood.assert_called_once()
            assert mock_antiflood.call_args[0][0] == getattr(mock_bot, expected_method)

    def test_send_text_propagates_exception(self):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)
        error = ApiTelegramException(
            function_name="send_message",
            result_json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"},
            result=None,
        )

        with patch("telebot.util.smart_split", return_value=["hello"]):
            with patch("telebot.util.antiflood", side_effect=error):
                with pytest.raises(ApiTelegramException):
                    sender.send_text("hello", "12345")


class TestTelegramCallbacks:
    def test_transcription_started(self):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)
        callbacks = TelegramCallbacks(sender=sender, telegram_bot=mock_bot)

        callbacks.transcription_started("12345")
        mock_bot.send_chat_action.assert_called_once_with(chat_id="12345", action="upload_voice")

    def test_submit_input_to_llm(self):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)
        callbacks = TelegramCallbacks(sender=sender, telegram_bot=mock_bot)

        callbacks.submit_input_to_llm("12345")
        mock_bot.send_chat_action.assert_called_once_with(chat_id="12345", action="typing")

    def test_echo_transcript(self):
        mock_bot = MagicMock()
        sender = TelegramSender(mock_bot)
        callbacks = TelegramCallbacks(sender=sender, telegram_bot=mock_bot)

        with patch("telebot.util.smart_split", return_value=["I heard: hello"]):
            with patch("telebot.util.antiflood"):
                callbacks.echo_transcript("12345", "hello")

    @patch("apps.channels.audio.convert_audio")
    @patch("httpx.get")
    def test_get_message_audio(self, mock_httpx_get, mock_convert_audio):
        mock_bot = MagicMock()
        mock_bot.get_file_url.return_value = "http://telegram.api/file/123"
        mock_response = MagicMock()
        mock_response.content = b"ogg_audio_data"
        mock_httpx_get.return_value = mock_response
        mock_convert_audio.return_value = BytesIO(b"wav_data")

        sender = TelegramSender(mock_bot)
        callbacks = TelegramCallbacks(sender=sender, telegram_bot=mock_bot)

        message = MagicMock()
        message.media_id = "file_123"

        result = callbacks.get_message_audio(message)

        mock_bot.get_file_url.assert_called_once_with("file_123")
        mock_httpx_get.assert_called_once_with("http://telegram.api/file/123")
        mock_response.raise_for_status.assert_called_once()
        mock_convert_audio.assert_called_once()
        assert result is not None
