from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.stages.core import QueryExtractionStage
from apps.channels.tests.channels.conftest import StubCallbacks, make_context
from apps.channels.tests.message_examples.base_messages import audio_message, text_message


class TestQueryExtractionStage:
    def setup_method(self):
        self.stage = QueryExtractionStage()

    def test_text_message_extracts_text(self):
        msg = text_message(message_text="Hello world")
        ctx = make_context(message=msg)

        self.stage(ctx)

        assert ctx.user_query == "Hello world"

    def test_voice_message_transcribes(self):
        msg = audio_message()
        callbacks = StubCallbacks()
        experiment = MagicMock()
        experiment.echo_transcript = False
        experiment.voice_provider.get_speech_service.return_value.supports_transcription = True
        experiment.voice_provider.get_speech_service.return_value.transcribe_audio.return_value = "transcribed text"
        ctx = make_context(message=msg, callbacks=callbacks, experiment=experiment)

        self.stage(ctx)

        assert ctx.user_query == "transcribed text"
        assert len(callbacks.transcription_started_calls) == 1
        assert len(callbacks.transcription_finished_calls) == 1

    def test_echo_transcript_when_enabled(self):
        msg = audio_message()
        callbacks = StubCallbacks()
        experiment = MagicMock()
        experiment.echo_transcript = True
        experiment.voice_provider.get_speech_service.return_value.supports_transcription = True
        experiment.voice_provider.get_speech_service.return_value.transcribe_audio.return_value = "heard this"
        ctx = make_context(message=msg, callbacks=callbacks, experiment=experiment)

        self.stage(ctx)

        assert len(callbacks.echo_transcript_calls) == 1
        assert callbacks.echo_transcript_calls[0][1] == "heard this"

    def test_no_echo_when_disabled(self):
        msg = audio_message()
        callbacks = StubCallbacks()
        experiment = MagicMock()
        experiment.echo_transcript = False
        experiment.voice_provider.get_speech_service.return_value.supports_transcription = True
        experiment.voice_provider.get_speech_service.return_value.transcribe_audio.return_value = "heard this"
        ctx = make_context(message=msg, callbacks=callbacks, experiment=experiment)

        self.stage(ctx)

        assert len(callbacks.echo_transcript_calls) == 0

    @patch("apps.channels.channels_v2.stages.core.audio_transcription_failure_notification")
    def test_transcription_failure_notifies(self, mock_notification):
        msg = audio_message()
        callbacks = StubCallbacks()
        experiment = MagicMock()
        experiment.voice_provider.get_speech_service.return_value.supports_transcription = True
        experiment.voice_provider.get_speech_service.return_value.transcribe_audio.side_effect = RuntimeError(
            "transcription failed"
        )
        ctx = make_context(message=msg, callbacks=callbacks, experiment=experiment)

        with pytest.raises(RuntimeError, match="transcription failed"):
            self.stage(ctx)

        mock_notification.assert_called_once()
        assert any("Voice transcription failed" in e for e in ctx.processing_errors)
