from unittest.mock import MagicMock, patch

import pytest

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.registry import PLATFORM_CHANNEL_CLASSES
from apps.channels.stages.core import (
    NO_SPEECH_MESSAGES,
    AttachmentHydrationStage,
    ChatMessageCreationStage,
    ErrorGuardStage,
    QueryExtractionStage,
)
from apps.channels.tests.channels.conftest import StubCallbacks, make_context
from apps.channels.tests.message_examples.base_messages import audio_message, text_message
from apps.channels.web_channel import WebChannel
from apps.chat.exceptions import NoSpeechDetected, NoSpeechReason, UserActionableError

_CHANNEL_CLASSES = sorted(
    {ChannelBase, ApiChannel, WebChannel, EvaluationChannel, *PLATFORM_CHANNEL_CLASSES.values()},
    key=lambda cls: cls.__name__,
)


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
    def test_transcription_failure_notifies_and_defers(self, mock_notification):
        """A genuine fault is deferred too, so the voice note still reaches the history.

        It is a fault rather than something the participant can act on, so it also
        notifies the team and is recorded as a processing error.
        """
        msg = audio_message()
        callbacks = StubCallbacks()
        experiment = MagicMock()
        experiment.voice_provider.get_speech_service.return_value.supports_transcription = True
        error = RuntimeError("transcription failed")
        experiment.voice_provider.get_speech_service.return_value.transcribe_audio.side_effect = error
        ctx = make_context(message=msg, callbacks=callbacks, experiment=experiment)

        self.stage(ctx)

        assert ctx.deferred_error is error
        assert ctx.user_query == ""
        mock_notification.assert_called_once()
        assert any("Voice transcription failed" in e for e in ctx.processing_errors)

    @pytest.mark.parametrize(
        "reason",
        [
            pytest.param(NoSpeechReason.SILENCE, id="silence"),
            pytest.param(NoSpeechReason.NOT_UNDERSTOOD, id="not-understood"),
        ],
    )
    @patch("apps.channels.stages.core.audio_transcription_failure_notification")
    def test_no_speech_defers_a_user_actionable_error(self, mock_notification, reason):
        """NoSpeechDetected is the speech service's vocabulary and is translated here.

        Deferred rather than raised so ChatMessageCreationStage still records the turn.
        """
        msg = audio_message()
        experiment = MagicMock()
        experiment.voice_provider.get_speech_service.return_value.supports_transcription = True
        experiment.voice_provider.get_speech_service.return_value.transcribe_audio.side_effect = NoSpeechDetected(
            reason
        )
        ctx = make_context(message=msg, callbacks=StubCallbacks(), experiment=experiment)

        self.stage(ctx)

        assert isinstance(ctx.deferred_error, UserActionableError)
        assert str(ctx.deferred_error) == NO_SPEECH_MESSAGES[reason]
        # Empty rather than None, so ChatMessageCreationStage runs and keeps the text empty.
        assert ctx.user_query == ""
        mock_notification.assert_not_called()
        assert ctx.processing_errors == []
        assert _span(ctx).set_outputs.called
        _span(ctx).mark_span_as_error.assert_not_called()

    @patch("apps.channels.stages.core.audio_transcription_failure_notification")
    def test_unavailable_transcription_defers_too(self, mock_notification):
        """Deferred for the same reason: the voice note belongs in the history either way.

        Nothing was tried and failed, so the team notification must stay silent.
        """
        msg = audio_message()
        experiment = MagicMock()
        experiment.voice_provider = None
        ctx = make_context(message=msg, callbacks=StubCallbacks(), experiment=experiment)

        self.stage(ctx)

        assert isinstance(ctx.deferred_error, UserActionableError)
        assert "not available" in str(ctx.deferred_error)
        assert ctx.user_query == ""
        mock_notification.assert_not_called()
        assert ctx.processing_errors == []
        _span(ctx).mark_span_as_error.assert_not_called()


class TestErrorGuardStage:
    def setup_method(self):
        self.stage = ErrorGuardStage()

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(UserActionableError(NO_SPEECH_MESSAGES[NoSpeechReason.NOT_UNDERSTOOD]), id="actionable"),
            pytest.param(RuntimeError("transcription failed"), id="fault"),
        ],
    )
    def test_raises_the_deferred_error(self, error):
        ctx = make_context(user_query="", deferred_error=error)

        with pytest.raises(type(error)) as exc_info:
            self.stage(ctx)

        assert exc_info.value is error

    def test_records_the_error_on_its_span(self):
        """The exception's type name alone would not say why the turn stopped."""
        ctx = make_context(user_query="", deferred_error=UserActionableError("no transcription here"))

        with pytest.raises(UserActionableError):
            self.stage(ctx)

        assert ctx.trace_service.span.call_args.kwargs["inputs"] == {"deferred_error": "no transcription here"}

    def test_does_not_run_for_an_ordinary_empty_query(self):
        """An attachment-only message with no caption reaches this stage with user_query == ""."""
        ctx = make_context(user_query="")

        self.stage(ctx)

        assert ctx.early_exit_response is None


class TestErrorGuardIsWired:
    """A deferred error is only ever raised by ErrorGuardStage.

    A pipeline that extracts a query but omits the guard would swallow the error entirely:
    the participant would get no reply, and a transcription fault would never be reported.
    The guard must also come after the turn is recorded, or the voice note it is about is
    missing from the history.
    """

    @pytest.mark.parametrize("channel_cls", _CHANNEL_CLASSES, ids=lambda cls: cls.__name__)
    def test_query_extraction_is_always_followed_by_the_guard(self, channel_cls):
        stub_self = MagicMock(attachment_hydration_stage_class=AttachmentHydrationStage)
        stage_types = [type(stage) for stage in channel_cls._build_pipeline(stub_self).core_stages]
        if QueryExtractionStage not in stage_types:
            pytest.skip(f"{channel_cls.__name__} does not extract a query")

        assert ErrorGuardStage in stage_types, f"{channel_cls.__name__} never raises a deferred error"
        assert stage_types.index(ErrorGuardStage) > stage_types.index(ChatMessageCreationStage)
