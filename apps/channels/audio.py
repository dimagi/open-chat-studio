from io import BytesIO

from pydub import AudioSegment


def convert_audio_to_wav(audio: BytesIO, source_format="ogg") -> BytesIO:
    """
    OpenAI doesn't support .ogg filetypes, so we need to convert to wav (supported). We do this in-memory
    """
    audio = AudioSegment.from_file(audio, format=source_format)
    # Convert to mono
    audio = audio.set_channels(1)

    # Export to WAV
    wav_audio = BytesIO()
    audio.export(wav_audio, format="wav")
    wav_audio.seek(0)
    wav_audio.name = "some_name.wav"

    return wav_audio


def convert_audio(audio: BytesIO, target_format: str, source_format="ogg") -> BytesIO:
    audio = AudioSegment.from_file(audio, format=source_format)
    # Convert to mono
    audio = audio.set_channels(1)

    new_audio = BytesIO()
    audio.export(new_audio, format=target_format)
    new_audio.seek(0)
    new_audio.name = f"some_name.{target_format}"
    return new_audio
