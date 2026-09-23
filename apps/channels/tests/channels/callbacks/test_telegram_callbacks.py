from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from apps.channels.datamodels import TelegramMessage
from apps.channels.telegram_channel import TelegramCallbacks


@pytest.fixture()
def telebot():
    return MagicMock()


@pytest.fixture()
def callbacks(telebot):
    return TelegramCallbacks(telegram_bot=telebot)


class TestGetMessageAudio:
    def _make_telegram_message(self, media_id="audio-file-id"):
        return TelegramMessage(
            participant_id="12345",
            message_text="",
            content_type="voice",
            media_id=media_id,
            message_id=1,
        )

    def test_downloads_and_converts_voice_audio(self, callbacks, telebot):
        telebot.get_file_url.return_value = "https://example.com/audio.ogg"
        wav_data = BytesIO(b"wav-bytes")

        with (
            patch("apps.channels.telegram_channel.httpx.get") as mock_get,
            patch("apps.channels.telegram_channel.audio.convert_audio") as mock_convert,
        ):
            response = MagicMock()
            response.content = b"ogg-bytes"
            response.raise_for_status = MagicMock()
            mock_get.return_value = response
            mock_convert.return_value = wav_data

            result = callbacks.get_message_audio(self._make_telegram_message("audio-file-id"))

        telebot.get_file_url.assert_called_once_with("audio-file-id")
        mock_get.assert_called_once_with("https://example.com/audio.ogg", timeout=30.0)
        response.raise_for_status.assert_called_once()
        mock_convert.assert_called_once()
        args, kwargs = mock_convert.call_args
        assert isinstance(args[0], BytesIO)
        assert args[0].getvalue() == b"ogg-bytes"
        assert kwargs == {"target_format": "wav", "source_format": "ogg"}
        assert result is wav_data

    def test_raises_for_non_telegram_message(self, callbacks):
        message = MagicMock()  # Not a TelegramMessage
        with pytest.raises(AssertionError):
            callbacks.get_message_audio(message)
