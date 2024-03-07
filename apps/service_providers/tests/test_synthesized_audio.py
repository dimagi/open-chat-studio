from io import BytesIO
from unittest import mock

import pytest

from apps.service_providers.speech_service import SynthesizedAudio


@pytest.mark.parametrize(
    ("source_format", "target_format", "codec", "conversion_expected"),
    [("mp3", "ogg", None, True), ("mp3", "mp3", None, False), ("mp3", "ogg", "libopus", True)],
)
def test_synthesized_audio(source_format, target_format, codec, conversion_expected):
    audio = SynthesizedAudio(audio=BytesIO(b"123"), duration=10.0, format=source_format)

    with mock.patch("apps.service_providers.speech_service.convert_audio") as convert_audio:
        convert_audio.return_value = BytesIO(b"321")
        audio.get_audio_bytes(target_format, codec)

        if conversion_expected:
            convert_audio.assert_called()
        else:
            convert_audio.assert_not_called()
