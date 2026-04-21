import logging
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from typing import IO, TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

import httpx
import pydantic

from apps.channels.audio import convert_audio
from apps.chat.exceptions import AudioSynthesizeException, AudioTranscriptionException, UserReportableError
from apps.experiments.models import SyntheticVoice
from apps.service_providers.intron import INTRON_BASE_URL

if TYPE_CHECKING:
    from openai import OpenAI

log = logging.getLogger("ocs.speech")


@dataclass
class SynthesizedAudio:
    audio: BytesIO
    duration: float
    format: str

    def get_audio_bytes(self, format: str, codec: str | None = None) -> bytes:
        """Returns the audio bytes in the specified `format` and `codec`. A conversion will always be triggered
        when `codec` is specified to ensure that this codec was used.
        """
        audio = self.audio
        if self.format != format or codec is not None:
            audio = convert_audio(audio=self.audio, target_format=format, source_format=self.format, codec=codec)
        return audio.getvalue()

    @property
    def content_type(self):
        mime_map = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "ogg": "audio/ogg",
        }
        return mime_map.get(self.format, f"audio/{self.format}")


class SpeechService(pydantic.BaseModel):
    _type: ClassVar[str]
    supports_transcription: ClassVar[bool] = False

    def synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        assert synthetic_voice.service == self._type
        try:
            return self._synthesize_voice(text, synthetic_voice)
        except Exception as e:
            log.exception(e)
            raise AudioSynthesizeException(f"Unable to synthesize audio with {self._type}: {e}") from e

    def transcribe_audio(self, audio: IO[bytes]) -> str:
        try:
            return self._transcribe_audio(audio)
        except Exception as e:
            log.exception(e)
            raise UserReportableError("Unable to transcribe audio") from e

    def _transcribe_audio(self, audio: IO[bytes]) -> str:
        raise NotImplementedError

    def _synthesize_voice(self, text, synthetic_voice) -> SynthesizedAudio:
        raise NotImplementedError


class AWSSpeechService(SpeechService):
    _type: ClassVar[str] = SyntheticVoice.AWS
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str

    def _synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        """
        Calls AWS Polly to convert the text to speech using the synthetic_voice
        """
        import boto3  # noqa: PLC0415 - TID253: heavy lib, slow startup
        from pydub import AudioSegment  # noqa: PLC0415 - lazy: optional audio processing lib

        polly_client = boto3.Session(
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.aws_region,
        ).client("polly")

        engine = "neural" if synthetic_voice.neural else "standard"
        response = polly_client.synthesize_speech(
            VoiceId=synthetic_voice.name, OutputFormat="mp3", Text=text, Engine=engine
        )

        audio_stream = response["AudioStream"]
        audio_data = audio_stream.read()

        audio_segment = AudioSegment.from_file(BytesIO(audio_data), format="mp3")
        duration_seconds = len(audio_segment) / 1000  # Convert milliseconds to seconds

        with closing(audio_stream):
            return SynthesizedAudio(audio=BytesIO(audio_data), duration=duration_seconds, format="mp3")


class AzureSpeechService(SpeechService):
    _type: ClassVar[str] = SyntheticVoice.Azure
    supports_transcription: ClassVar[bool] = True
    azure_subscription_key: str
    azure_region: str

    def _synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        """
        Calls Azure's cognitive speech services to convert the text to speech using the synthetic_voice
        """
        # keep heavy imports inline
        import azure.cognitiveservices.speech as speechsdk  # noqa: PLC0415 - lazy: optional provider dep (Azure speech SDK)
        from pydub import AudioSegment  # noqa: PLC0415 - lazy: optional audio processing lib

        speech_config = speechsdk.SpeechConfig(subscription=self.azure_subscription_key, region=self.azure_region)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            # Create an audio configuration that points to the output audio file
            audio_config = speechsdk.audio.AudioConfig(filename=temp_file.name)

            # Setup voice font (Azure refers to synthetic voices as voice fonts)
            speech_config.speech_synthesis_voice_name = f"{synthetic_voice.language_code}-{synthetic_voice.name}"

            # Create a speech synthesizer
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

            # Synthesize the text
            result = synthesizer.speak_text(text)

            # Check if synthesis was successful
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_segment = AudioSegment.from_file(
                    temp_file.name, format="wav"
                )  # Azure returns audio in WAV format
                duration_seconds = len(audio_segment) / 1000  # Convert milliseconds to seconds

                with open(temp_file.name, "rb") as f:
                    file_content = f.read()

                return SynthesizedAudio(audio=BytesIO(file_content), duration=duration_seconds, format="wav")
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation_details = result.cancellation_details
                msg = f"Azure speech synthesis failed: {cancellation_details.reason.name}"
                if cancellation_details.reason == speechsdk.CancellationReason.Error:
                    if cancellation_details.error_details:
                        msg += f". Error details: {cancellation_details.error_details}"
                raise AudioSynthesizeException(msg)
            raise AudioSynthesizeException(f"Unexpected result: {result}")

    def _transcribe_audio(self, audio: IO[bytes]) -> str:
        # keep heavy imports inline
        import azure.cognitiveservices.speech as speechsdk  # noqa: PLC0415 - lazy: optional provider dep (Azure speech SDK)

        speech_config = speechsdk.SpeechConfig(subscription=self.azure_subscription_key, region=self.azure_region)
        speech_config.speech_recognition_language = "en-US"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            audio.seek(0)
            temp_file.write(audio.read())
            temp_file.seek(0)

            audio_config = speechsdk.audio.AudioConfig(filename=temp_file.name)
            speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
            result = speech_recognizer.recognize_once_async().get()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            return result.text
        elif result.reason == speechsdk.ResultReason.NoMatch:
            reason = result.no_match_details.reason
            raise AudioTranscriptionException(f"No speech could be recognized: {reason}")
        elif result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            msg = f"Azure speech transcription failed: {cancellation_details.reason.name}"
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                if cancellation_details.error_details:
                    msg += f". Error details: {cancellation_details.error_details}"
            raise AudioTranscriptionException(msg)
        raise AudioTranscriptionException(f"Unexpected result: {result}")


class OpenAISpeechService(SpeechService):
    _type: ClassVar[str] = SyntheticVoice.OpenAI
    supports_transcription: ClassVar[bool] = True
    openai_api_key: str
    openai_api_base: str | None = None
    openai_organization: str | None = None

    @property
    def _client(self) -> "OpenAI":
        # keep heavy imports inline
        from openai import OpenAI  # noqa: PLC0415 - lazy: optional provider dep (OpenAI speech)

        return OpenAI(api_key=self.openai_api_key, organization=self.openai_organization, base_url=self.openai_api_base)

    def _synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        """
        Calls OpenAI to convert the text to speech using the synthetic_voice
        """
        # keep heavy imports inline
        from pydub import AudioSegment  # noqa: PLC0415 - lazy: optional audio processing lib

        response = self._client.audio.speech.create(model="gpt-4o-mini-tts", voice=synthetic_voice.name, input=text)
        audio_data = response.read()

        audio_segment = AudioSegment.from_file(BytesIO(audio_data), format="mp3")
        duration_seconds = len(audio_segment) / 1000  # Convert milliseconds to seconds
        return SynthesizedAudio(audio=BytesIO(audio_data), duration=duration_seconds, format="mp3")

    def _transcribe_audio(self, audio: IO[bytes]) -> str:
        transcript = self._client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio,
        )
        return transcript.text


class ElevenLabsSpeechService(SpeechService):
    _type: ClassVar[str] = SyntheticVoice.ElevenLabs
    supports_transcription: ClassVar[bool] = True
    _output_format: ClassVar[str] = "mp3_44100_128"
    _stt_model: ClassVar[str] = "scribe_v2"
    elevenlabs_api_key: str
    elevenlabs_model: str = "eleven_multilingual_v2"

    @property
    def _client(self):
        from elevenlabs.client import ElevenLabs as ElevenLabsClient  # noqa: PLC0415 - lazy: optional provider dep

        return ElevenLabsClient(api_key=self.elevenlabs_api_key)

    def _synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        from pydub import AudioSegment  # noqa: PLC0415 - lazy: optional audio processing lib

        audio_iter = self._client.text_to_speech.convert(
            voice_id=synthetic_voice.external_id,
            model_id=self.elevenlabs_model,
            text=text,
            output_format=self._output_format,
        )
        audio_data = b"".join(audio_iter)
        audio_segment = AudioSegment.from_file(BytesIO(audio_data), format="mp3")
        duration_seconds = len(audio_segment) / 1000
        return SynthesizedAudio(audio=BytesIO(audio_data), duration=duration_seconds, format="mp3")

    def _transcribe_audio(self, audio: IO[bytes]) -> str:
        result = self._client.speech_to_text.convert(
            file=audio,
            model_id=self._stt_model,
        )
        return result.text


class OpenAIVoiceEngineSpeechService(SpeechService):
    _type: ClassVar[str] = SyntheticVoice.OpenAIVoiceEngine
    supports_transcription: ClassVar[bool] = True
    openai_api_key: str
    openai_api_base: str | None = None
    openai_organization: str | None = None

    @property
    def _client(self) -> "OpenAI":
        from openai import OpenAI  # noqa: PLC0415 - lazy: optional provider dep (OpenAI speech)

        return OpenAI(api_key=self.openai_api_key, organization=self.openai_organization, base_url=self.openai_api_base)

    def _synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        """
        Uses the voice sample from `synthetic_voice` and calls OpenAI to synthesize audio with the sample voice
        """
        # keep heavy imports inline
        from pydub import AudioSegment  # noqa: PLC0415 - lazy: optional audio processing lib

        sample_audio = synthetic_voice.file

        url = "https://api.openai.com/v1/audio/synthesize"
        headers = {"Authorization": f"Bearer {self.openai_api_key}"}

        files = {"reference_audio": sample_audio.file}
        data = {
            "model": "gpt-4o-mini-tts",
            "text": text,
            "speed": "1.0",
            "response_format": "mp3",
        }
        response = httpx.post(url, headers=headers, data=data, files=files)

        if response.status_code == 200:
            audio_data = BytesIO(response.content)
            audio_segment = AudioSegment.from_file(audio_data, format="mp3")
            audio_data.seek(0)
            return SynthesizedAudio(audio=audio_data, duration=audio_segment.duration_seconds, format="mp3")
        else:
            msg = f"Error synthesizing voice with OpenAI Voice Engine. Response status: {response.status_code}."
            raise AudioSynthesizeException(msg)

    def _transcribe_audio(self, audio: IO[bytes]) -> str:
        transcript = self._client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio,
        )
        return transcript.text


class IntronSpeechService(SpeechService):
    _type: ClassVar[str] = SyntheticVoice.Intron
    intron_api_key: str
    poll_interval_seconds: float = 1.0
    poll_max_attempts: int = 120  # 2 minutes at 1s interval
    # Per-request timeout. Bounds enqueue + each status poll + audio download independently.
    request_timeout_seconds: float = 30.0

    _TERMINAL_FAILURE_STATUS: ClassVar[str] = "TTS_TEXT_AUDIO_PROCESSING_FAILED"
    _SUCCESS_STATUS: ClassVar[str] = "TTS_TEXT_AUDIO_GENERATED"

    def _synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        from pydub import AudioSegment  # noqa: PLC0415 - lazy: optional audio processing lib

        headers = {"Authorization": f"Bearer {self.intron_api_key}"}

        enqueue = httpx.post(
            f"{INTRON_BASE_URL}/tts/v1/enqueue",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "text": text,
                "voice_accent": synthetic_voice.name,
                "voice_gender": synthetic_voice.gender,
            },
            timeout=self.request_timeout_seconds,
        )
        if enqueue.status_code != 200:
            raise AudioSynthesizeException(
                f"Intron enqueue failed: status={enqueue.status_code} body={enqueue.text[:200]}"
            )
        # Real response shape: {"data": {"text_id": "..."}, "message": "...", "status": "Ok"}
        enqueue_data = enqueue.json().get("data") or {}
        text_id = enqueue_data.get("text_id")
        if not text_id:
            raise AudioSynthesizeException(f"Intron enqueue response missing data.text_id. Body: {enqueue.text[:200]}")

        audio_url = self._poll_until_ready(text_id, headers)
        audio_bytes, audio_format = self._download_audio(audio_url)

        audio_segment = AudioSegment.from_file(BytesIO(audio_bytes), format=audio_format)
        duration_seconds = len(audio_segment) / 1000
        return SynthesizedAudio(audio=BytesIO(audio_bytes), duration=duration_seconds, format=audio_format)

    def _poll_until_ready(self, text_id: str, headers: dict) -> str:
        """Poll the status endpoint until the audio is generated; return the audio URL.

        Real response shape:
            {
              "data": {
                "audio_duration_in_seconds": <float>,
                "audio_path": <str>,              # populated once generated
                "processing_status": "TTS_TEXT_PROCESSING" | "TTS_TEXT_AUDIO_GENERATED" | ...
              },
              "message": <str>,
              "status": "Ok"
            }
        """
        status_url = f"{INTRON_BASE_URL}/tts/v1/status/{text_id}"
        for _ in range(self.poll_max_attempts):
            resp = httpx.get(status_url, headers=headers, timeout=self.request_timeout_seconds)
            if resp.status_code != 200:
                raise AudioSynthesizeException(
                    f"Intron status poll failed: status={resp.status_code} body={resp.text[:200]}"
                )
            data = resp.json().get("data") or {}
            processing_status = data.get("processing_status")
            if processing_status == self._SUCCESS_STATUS:
                audio_url = data.get("audio_path")
                if not audio_url:
                    raise AudioSynthesizeException(
                        f"Intron reported success but no audio_path in status response. "
                        f"Data keys: {sorted(data.keys())}"
                    )
                return audio_url
            if processing_status == self._TERMINAL_FAILURE_STATUS:
                raise AudioSynthesizeException(f"Intron synthesis failed: {resp.text[:500]}")
            time.sleep(self.poll_interval_seconds)
        raise AudioSynthesizeException(
            f"Intron synthesis did not complete within {self.poll_max_attempts} poll attempts"
        )

    def _download_audio(self, url: str) -> tuple[bytes, str]:
        # The audio URL points to an intron-owned S3 bucket, not the intron API itself.
        # S3 rejects unrelated Authorization headers with a 400, so don't forward our
        # Bearer credentials here — the URL itself carries whatever authorization is needed.
        resp = httpx.get(url, timeout=self.request_timeout_seconds)
        if resp.status_code != 200:
            raise AudioSynthesizeException(f"Intron audio download failed: status={resp.status_code}")
        content_type = resp.headers.get("Content-Type", "").lower()
        # Parse the URL path so the extension check survives query strings on signed/presigned URLs.
        path = urlparse(url).path.lower()
        if "wav" in content_type or path.endswith(".wav"):
            audio_format = "wav"
        elif "ogg" in content_type or path.endswith(".ogg"):
            audio_format = "ogg"
        else:
            audio_format = "mp3"
        return resp.content, audio_format
