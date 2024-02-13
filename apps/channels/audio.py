from io import BytesIO

from pydub import AudioSegment


def convert_audio(audio: BytesIO, target_format: str, source_format="ogg", codec=None) -> BytesIO:
    audio = AudioSegment.from_file(audio, format=source_format)
    # Convert to mono
    audio = audio.set_channels(1)

    convertion_kwargs = {}
    if codec:
        convertion_kwargs["codec"] = codec

    new_audio = BytesIO()
    audio.export(new_audio, format=target_format, **convertion_kwargs)
    new_audio.seek(0)
    new_audio.name = f"some_name.{target_format}"
    return new_audio
