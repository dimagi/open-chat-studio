import tempfile
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from typing import ClassVar

import azure.cognitiveservices.speech as speechsdk
import boto3
import pydantic
from pydub import AudioSegment

from apps.channels.audio import convert_audio
from apps.chat.exceptions import AudioSynthesizeException
from apps.experiments.models import SyntheticVoice


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


class SpeechService(pydantic.BaseModel):
    _type: ClassVar[str]
    supports_transcription: ClassVar[bool] = False

    def synthesize_voice(self, text: str, synthetic_voice: SyntheticVoice) -> SynthesizedAudio:
        assert synthetic_voice.service == self._type
        try:
            return self._synthesize_voice(text, synthetic_voice)
        except Exception as e:
            raise AudioSynthesizeException(f"Unable to synthesize audio with {self._type}: {e}") from e

    def transcribe_audio(self, audio: BytesIO) -> str:
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

    def transcribe_audio(self, audio: BytesIO) -> str:
        speech_config = speechsdk.SpeechConfig(subscription=self.azure_subscription_key, region=self.azure_region)
        speech_config.speech_recognition_language = "en-US"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_file.write(audio.getbuffer())
            temp_file.seek(0)

            audio_config = speechsdk.audio.AudioConfig(filename=temp_file.name)
            speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
            result = speech_recognizer.recognize_once_async().get()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            return result.text
        elif result.reason == speechsdk.ResultReason.NoMatch:
            reason = result.no_match_details.reason
            raise AudioSynthesizeException(f"No speech could be recognized {reason}")
        elif result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            msg = f"Azure speech transcription failed: {cancellation_details.reason.name}"
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                if cancellation_details.error_details:
                    msg += f". Error details: {cancellation_details.error_details}"
            raise AudioSynthesizeException(msg)
