import os
import tempfile
from contextlib import closing
from io import BytesIO
from typing import Tuple

import azure.cognitiveservices.speech as speechsdk
import boto3
import openai
from django.conf import settings
from pydub import AudioSegment

from apps.chat.exceptions import AudioSynthesizeException
from apps.experiments.models import SyntheticVoice


def get_transcript(audio: BytesIO) -> str:
    transcript = openai.Audio.transcribe(model="whisper-1", file=audio)
    return transcript["text"]


def synthesize_voice(text: str, synthetic_voice: SyntheticVoice) -> Tuple[BytesIO, float]:
    if synthetic_voice.service == "AWS":
        return aws_synthesize_voice(text, synthetic_voice)
    elif synthetic_voice.service == "Azure":
        return azure_synthesize_voice(text, synthetic_voice)

    raise AudioSynthesizeException(f"Unrecognized audio service: {synthetic_voice.service}")


def azure_synthesize_voice(text: str, synthetic_voice: SyntheticVoice) -> Tuple[BytesIO, float]:
    """
    Calls Azure's cognitive speech services to convert the text to speech using the synthetic_voice
    """
    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=settings.AZURE_SUBSCRIPTION_KEY, region=settings.AZURE_REGION
        )

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

                return BytesIO(file_content), duration_seconds
            else:
                raise AudioSynthesizeException(f"Azure audio synthesis failed with reason: {result.reason}. Req")

    except Exception as e:
        raise AudioSynthesizeException(f"Unable to synthesize audio with Azure: {e}")


def aws_synthesize_voice(text: str, synthetic_voice: SyntheticVoice) -> Tuple[BytesIO, float]:
    """
    Calls AWS Polly to convert the text to speech using the synthetic_voice
    """
    try:
        polly_client = boto3.Session(
            aws_access_key_id=settings.AWS_POLLY_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_POLLY_SECRET_KEY,
            region_name=settings.AWS_POLLY_REGION,
        ).client("polly")

        engine = "neural" if synthetic_voice.neural else "standard"
        response = polly_client.synthesize_speech(
            VoiceId=synthetic_voice.name, OutputFormat="mp3", Text=text, Engine=engine
        )

        audio_stream = response["AudioStream"]
        audio_data = audio_stream.read()

        audio_segment = AudioSegment.from_file(BytesIO(audio_data), format="mp3")
        duration_seconds = len(audio_segment) / 1000  # Convert milliseconds to seconds

        with closing(audio_stream) as stream:
            return BytesIO(audio_data), duration_seconds
    except Exception as e:
        raise AudioSynthesizeException(f"Unable to synthesize audio with AWS Polly: {e}")


def convert_ogg_to_wav(ogg_audio: BytesIO) -> BytesIO:
    """
    OpenAI doesn't support .ogg filetypes, so we need to convert to wav (supported). We do this in-memory
    """
    audio = AudioSegment.from_file(ogg_audio, format="ogg")
    # Convert to mono
    audio = audio.set_channels(1)

    # Export to WAV
    wav_audio = BytesIO()
    audio.export(wav_audio, format="wav")
    wav_audio.seek(0)
    wav_audio.name = "some_name.wav"

    return wav_audio
