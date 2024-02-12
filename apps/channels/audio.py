from io import BytesIO

from pydub import AudioSegment


def convert_audio(audio: BytesIO, target_format: str, source_format="ogg") -> BytesIO:
    audio = AudioSegment.from_file(audio, format=source_format)
    # Convert to mono
    audio = audio.set_channels(1)

    new_audio = BytesIO()
    audio.export(new_audio, format=target_format)
    new_audio.seek(0)
    new_audio.name = f"some_name.{target_format}"
    return new_audio
