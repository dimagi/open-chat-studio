from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage


class ChannelCallbacks:
    """Base class for channel-specific callback hooks.

    Default implementations are no-ops. Channels override the methods they care about.
    Methods that target a user receive `recipient: str` -- not the full context.
    """

    def transcription_started(self, recipient: str) -> None:
        """Called when voice transcription starts (e.g. show 'uploading voice' indicator)."""

    def transcription_finished(self, recipient: str, transcript: str) -> None:
        """Called when voice transcription completes."""

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        """Send the transcript back to the user."""

    def submit_input_to_llm(self, recipient: str) -> None:
        """Called before LLM invocation (e.g. show 'typing' indicator)."""

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        """Retrieve audio content from the inbound message. Must be overridden
        by channels that support voice."""
        raise NotImplementedError("Channel must implement audio retrieval")
