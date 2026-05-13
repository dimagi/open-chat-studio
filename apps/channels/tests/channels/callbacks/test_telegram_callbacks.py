from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.telegram_channel import TelegramCallbacks
from apps.channels.datamodels import TelegramMessage


@pytest.fixture()
def telebot():
    return MagicMock()


@pytest.fixture()
def callbacks(telebot):
    return TelegramCallbacks(telegram_bot=telebot)


class TestLifecycleHooks:
    def test_transcription_started_sends_upload_voice_action(self, callbacks, telebot):
        callbacks.transcription_started("12345")
        telebot.send_chat_action.assert_called_once_with(chat_id="12345", action="upload_voice")

    def test_submit_input_to_llm_sends_typing_action(self, callbacks, telebot):
        callbacks.submit_input_to_llm("12345")
        telebot.send_chat_action.assert_called_once_with(chat_id="12345", action="typing")

    def test_echo_transcript_sends_message(self, callbacks, telebot):
        callbacks.echo_transcript("12345", "the transcript")
        telebot.send_message.assert_called_once_with("12345", text="I heard: the transcript")

    def test_transcription_finished_is_noop(self, callbacks, telebot):
        # Inherited no-op from base ChannelCallbacks; should not call the telebot.
        callbacks.transcription_finished("12345", "transcript")
        telebot.send_chat_action.assert_not_called()
        telebot.send_message.assert_not_called()


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
            patch("apps.channels.channels_v2.telegram_channel.httpx.get") as mock_get,
            patch("apps.channels.channels_v2.telegram_channel.audio.convert_audio") as mock_convert,
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
