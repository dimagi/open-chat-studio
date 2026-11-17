import logging
from contextlib import contextmanager
from io import BytesIO
from unittest import mock

import azure.cognitiveservices.speech as speechsdk
import pytest

from apps.chat.exceptions import (
    AudioSynthesizeException,
    AudioTranscriptionException,
    NoSpeechDetected,
    NoSpeechReason,
)
from apps.experiments.models import SyntheticVoice
from apps.service_providers.speech_service import (
    AzureSpeechService,
    ElevenLabsSpeechService,
    GeminiSpeechService,
    OpenAISpeechService,
    OpenAIVoiceEngineSpeechService,
    SpeechService,
    _gemini_transcript,
)


def _azure_service():
    return AzureSpeechService(azure_subscription_key="key", azure_region="eastus")


@contextmanager
def _azure_returning(result):
    """Run the Azure recognizer against a canned SpeechRecognitionResult."""
    with (
        mock.patch.object(speechsdk, "SpeechConfig"),
        mock.patch.object(speechsdk.audio, "AudioConfig"),
        mock.patch.object(speechsdk, "SpeechRecognizer") as recognizer_cls,
    ):
        recognizer_cls.return_value.recognize_once_async.return_value.get.return_value = result
        yield


def _no_match_result(reason):
    result = mock.Mock()
    result.reason = speechsdk.ResultReason.NoMatch
    result.no_match_details.reason = reason
    return result


class TestAzureNoMatch:
    @pytest.mark.parametrize(
        ("no_match_reason", "expected"),
        [
            pytest.param(
                speechsdk.NoMatchReason.NotRecognized, NoSpeechReason.NOT_UNDERSTOOD, id="not-recognized-speech-heard"
            ),
            pytest.param(
                speechsdk.NoMatchReason.InitialSilenceTimeout, NoSpeechReason.SILENCE, id="initial-silence-timeout"
            ),
            pytest.param(speechsdk.NoMatchReason.EndSilenceTimeout, NoSpeechReason.SILENCE, id="end-silence-timeout"),
            pytest.param(
                speechsdk.NoMatchReason.InitialBabbleTimeout, NoSpeechReason.SILENCE, id="babble-timeout-only-noise"
            ),
            pytest.param(
                speechsdk.NoMatchReason.KeywordNotRecognized, NoSpeechReason.SILENCE, id="keyword-not-recognized"
            ),
        ],
    )
    def test_no_match_reasons_map_to_a_speech_reason(self, no_match_reason, expected):
        with _azure_returning(_no_match_result(no_match_reason)), pytest.raises(NoSpeechDetected) as exc_info:
            _azure_service().transcribe_audio(BytesIO(b"audio"))

        assert exc_info.value.reason == expected

    def test_every_no_match_reason_is_covered(self):
        """A new SDK reason should show up here rather than silently take a default."""
        covered = {
            speechsdk.NoMatchReason.NotRecognized,
            speechsdk.NoMatchReason.InitialSilenceTimeout,
            speechsdk.NoMatchReason.EndSilenceTimeout,
            speechsdk.NoMatchReason.InitialBabbleTimeout,
            speechsdk.NoMatchReason.KeywordNotRecognized,
        }
        assert set(speechsdk.NoMatchReason) == covered

    def test_recognized_speech_is_returned(self):
        result = mock.Mock()
        result.reason = speechsdk.ResultReason.RecognizedSpeech
        result.text = "hello there"

        with _azure_returning(result):
            assert _azure_service().transcribe_audio(BytesIO(b"audio")) == "hello there"

    def test_cancelled_is_still_an_unexpected_failure(self):
        result = mock.Mock()
        result.reason = speechsdk.ResultReason.Canceled
        result.cancellation_details.reason = speechsdk.CancellationReason.Error
        result.cancellation_details.error_details = "Invalid subscription key"

        with _azure_returning(result), pytest.raises(AudioTranscriptionException, match="Unable to transcribe audio"):
            _azure_service().transcribe_audio(BytesIO(b"audio"))


class TestBlankTranscript:
    """Providers that signal silence with an empty transcript rather than an error."""

    @pytest.mark.parametrize(
        "service",
        [
            pytest.param(AzureSpeechService(azure_subscription_key="key", azure_region="eastus"), id="azure"),
            pytest.param(OpenAISpeechService(openai_api_key="key"), id="openai"),
            pytest.param(OpenAIVoiceEngineSpeechService(openai_api_key="key"), id="openai-voice-engine"),
            pytest.param(ElevenLabsSpeechService(elevenlabs_api_key="key"), id="elevenlabs"),
            pytest.param(GeminiSpeechService(gemini_api_key="key"), id="gemini"),
        ],
    )
    @pytest.mark.parametrize(
        "transcript",
        [pytest.param("", id="empty"), pytest.param("   \n ", id="whitespace"), pytest.param(None, id="none")],
    )
    def test_blank_transcript_raises_silence(self, service, transcript):
        with mock.patch.object(type(service), "_transcribe_audio", return_value=transcript):
            with pytest.raises(NoSpeechDetected) as exc_info:
                service.transcribe_audio(BytesIO(b"audio"))

        assert exc_info.value.reason == NoSpeechReason.SILENCE

    def test_blank_transcript_names_the_provider_in_the_log(self, caplog):
        """The only operator-visible signal that a provider is returning nothing but blanks."""
        service = OpenAISpeechService(openai_api_key="key")

        with caplog.at_level(logging.INFO, logger="ocs.speech"):
            with mock.patch.object(OpenAISpeechService, "_transcribe_audio", return_value=""):
                with pytest.raises(NoSpeechDetected):
                    service.transcribe_audio(BytesIO(b"audio"))

        assert OpenAISpeechService._type in caplog.text


class StubSpeechService(SpeechService):
    """Base-class behaviour, isolated from any provider SDK."""

    _type = "stub"
    supports_transcription = True
    result: object = None

    def _transcribe_audio(self, audio):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class TestTranscribeAudioErrorPolicy:
    def test_no_speech_detected_keeps_its_reason(self):
        """NoSpeechDetected must survive intact so the pipeline can tell it apart."""
        service = StubSpeechService(result=NoSpeechDetected(NoSpeechReason.NOT_UNDERSTOOD))

        with pytest.raises(NoSpeechDetected) as exc_info:
            service.transcribe_audio(BytesIO(b"audio"))

        assert exc_info.value.reason == NoSpeechReason.NOT_UNDERSTOOD

    def test_other_failures_are_still_flattened(self):
        service = StubSpeechService(result=RuntimeError("connection reset"))

        with pytest.raises(AudioTranscriptionException, match="Unable to transcribe audio"):
            service.transcribe_audio(BytesIO(b"audio"))

    def test_transcript_is_returned_unchanged(self):
        service = StubSpeechService(result="  hello  ")

        assert service.transcribe_audio(BytesIO(b"audio")) == "  hello  "


class TestGemini:
    def test_sends_the_wav_and_prompt_to_the_configured_model(self):
        service = GeminiSpeechService(gemini_api_key="key", gemini_transcription_model="gemini-3.5-flash-lite")
        response = mock.Mock(json=mock.Mock(return_value={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}))

        with mock.patch("apps.service_providers.speech_service.httpx.post", return_value=response) as post:
            assert service.transcribe_audio(BytesIO(b"RIFF")) == "hi"

        url, kwargs = post.call_args.args[0], post.call_args.kwargs
        assert url.endswith("/models/gemini-3.5-flash-lite:generateContent")
        assert kwargs["headers"] == {"x-goog-api-key": "key"}
        audio_part, prompt_part = kwargs["json"]["contents"][0]["parts"]
        assert audio_part["inline_data"] == {"mime_type": "audio/wav", "data": "UklGRg=="}
        assert "transcript" in prompt_part["text"].lower()

    @pytest.mark.parametrize(
        ("parts", "expected"),
        [
            pytest.param([{"audioTranscription": {"text": "hello there"}}], "hello there", id="transcription-model"),
            pytest.param([{"text": "hello there"}], "hello there", id="general-model"),
            pytest.param(
                [{"text": "thinking about it", "thought": True}, {"text": "hello there"}],
                "hello there",
                id="reasoning-is-skipped",
            ),
            pytest.param([], "", id="no-parts"),
        ],
    )
    def test_reads_the_transcript_from_either_model_kind(self, parts, expected):
        assert _gemini_transcript({"candidates": [{"content": {"parts": parts}}]}) == expected

    def test_a_blocked_request_is_a_transcription_error_not_silence(self):
        service = GeminiSpeechService(gemini_api_key="key")
        response = mock.Mock(json=mock.Mock(return_value={"promptFeedback": {"blockReason": "OTHER"}}))

        with mock.patch("apps.service_providers.speech_service.httpx.post", return_value=response):
            with pytest.raises(AudioTranscriptionException) as raised:
                service.transcribe_audio(BytesIO(b"RIFF"))

        assert type(raised.value) is AudioTranscriptionException

    def test_a_voice_from_another_service_fails_as_a_synthesis_error(self):
        voice = SyntheticVoice(name="Joanna", service=SyntheticVoice.AWS)

        with pytest.raises(AudioSynthesizeException):
            GeminiSpeechService(gemini_api_key="key").synthesize_voice("hi", voice)
