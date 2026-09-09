from unittest.mock import MagicMock, patch

import pytest

from apps.channels.stages.core import NoSpeechGuardStage, QueryExtractionStage
from apps.channels.tests.channels.conftest import StubCallbacks, make_context
from apps.channels.tests.message_examples.base_messages import audio_message, text_message
from apps.chat.exceptions import NoSpeechDetected, NoSpeechReason


def _span(ctx):
    """The span mock the stage wrote to, as entered by ProcessingStage.__call__."""
    return ctx.trace_service.span.return_value.__enter__.return_value


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

    @patch("apps.channels.stages.core.audio_transcription_failure_notification")
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
        # The raise tears the span down, which is what marks the trace as errored.
        assert not _span(ctx).set_outputs.called

    @pytest.mark.parametrize(
        "reason",
        [
            pytest.param(NoSpeechReason.SILENCE, id="silence"),
            pytest.param(NoSpeechReason.NOT_UNDERSTOOD, id="not-understood"),
        ],
    )
    @patch("apps.channels.stages.core.audio_transcription_failure_notification")
    def test_no_speech_defers_instead_of_raising(self, mock_notification, reason):
        """Deferred so ChatMessageCreationStage still records the turn."""
        msg = audio_message()
        experiment = MagicMock()
        experiment.voice_provider.get_speech_service.return_value.supports_transcription = True
        experiment.voice_provider.get_speech_service.return_value.transcribe_audio.side_effect = NoSpeechDetected(
            reason
        )
        ctx = make_context(message=msg, callbacks=StubCallbacks(), experiment=experiment)

        self.stage(ctx)

        assert ctx.no_speech_reason == reason
        # Empty rather than None, so ChatMessageCreationStage runs and keeps the text empty.
        assert ctx.user_query == ""
        mock_notification.assert_not_called()
        assert ctx.processing_errors == []
        assert _span(ctx).set_outputs.called
        _span(ctx).mark_span_as_error.assert_not_called()


class TestNoSpeechGuardStage:
    def setup_method(self):
        self.stage = NoSpeechGuardStage()

    def test_raises_the_deferred_reason(self):
        ctx = make_context(user_query="", no_speech_reason=NoSpeechReason.NOT_UNDERSTOOD)

        with pytest.raises(NoSpeechDetected) as exc_info:
            self.stage(ctx)

        assert exc_info.value.reason == NoSpeechReason.NOT_UNDERSTOOD

    def test_does_not_run_for_an_ordinary_empty_query(self):
        """An attachment-only message with no caption reaches this stage with user_query == ""."""
        ctx = make_context(user_query="")

        self.stage(ctx)

        assert ctx.early_exit_response is None
