from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.files.models import File
    from apps.service_providers.speech_service import SynthesizedAudio


class ChannelSender:
    """Abstracts how a channel delivers messages to the user.

    Sender implementations encapsulate platform-specific sending details
    (e.g., from_number, bot token, thread_ts) at construction time.
    The send methods receive only the data that varies per call.

    Default implementations raise NotImplementedError. Channels only override
    the methods their capabilities support -- the capabilities layer gates which
    methods actually get called at runtime.
    """

    def send_text(self, text: str, recipient: str) -> None:
        raise NotImplementedError

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        raise NotImplementedError

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        raise NotImplementedError
