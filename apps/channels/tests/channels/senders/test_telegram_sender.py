from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.telegram_channel import TelegramSender


@pytest.fixture()
def telebot():
    return MagicMock()


@pytest.fixture()
def sender(telebot):
    return TelegramSender(telegram_bot=telebot)


class TestSendText:
    def test_sends_text_message(self, sender, telebot):
        with patch("apps.channels.channels_v2.telegram_channel.antiflood") as mock_antiflood:
            sender.send_text("hello", "12345")
        mock_antiflood.assert_called_once_with(telebot.send_message, "12345", text="hello")

    def test_long_text_is_split_into_chunks(self, sender, telebot):
        # smart_split limits chunks to ~4096 chars; build a payload large enough to force splitting
        long_text = "a" * 8500
        with patch("apps.channels.channels_v2.telegram_channel.antiflood") as mock_antiflood:
            sender.send_text(long_text, "12345")
        assert mock_antiflood.call_count >= 2
        for call in mock_antiflood.call_args_list:
            args, kwargs = call
            assert args[0] is telebot.send_message
            assert args[1] == "12345"
            assert kwargs["text"]

    def test_propagates_send_errors(self, sender):
        with patch("apps.channels.channels_v2.telegram_channel.antiflood", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                sender.send_text("hello", "12345")


class TestSendVoice:
    def test_sends_voice_with_duration(self, sender, telebot):
        audio = MagicMock()
        audio.audio = b"audio-bytes"
        audio.duration = 5
        with patch("apps.channels.channels_v2.telegram_channel.antiflood") as mock_antiflood:
            sender.send_voice(audio, "12345")
        mock_antiflood.assert_called_once_with(telebot.send_voice, "12345", voice=audio.audio, duration=audio.duration)


class TestSendFile:
    @pytest.mark.parametrize(
        ("content_type", "expected_method_attr", "expected_kwarg"),
        [
            ("image/jpeg", "send_photo", "photo"),
            ("video/mp4", "send_video", "video"),
            ("audio/mpeg", "send_audio", "audio"),
            ("application/pdf", "send_document", "document"),
            ("application/octet-stream", "send_document", "document"),
            ("", "send_document", "document"),
        ],
    )
    def test_dispatches_to_correct_telebot_method(
        self, sender, telebot, content_type, expected_method_attr, expected_kwarg
    ):
        file = MagicMock()
        file.content_type = content_type
        file.file = b"file-bytes"

        with patch("apps.channels.channels_v2.telegram_channel.antiflood") as mock_antiflood:
            sender.send_file(file, "12345", session_id=42)

        expected_method = getattr(telebot, expected_method_attr)
        mock_antiflood.assert_called_once_with(expected_method, "12345", **{expected_kwarg: b"file-bytes"})
