from io import BytesIO

import openai
from pydub import AudioSegment


def get_transcript(audio: BytesIO) -> str:
    transcript = openai.Audio.transcribe(model="whisper-1", file=audio)
    return transcript["text"]


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
