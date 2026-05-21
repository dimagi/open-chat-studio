from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from django.db.models import Q

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext
    from apps.channels.datamodels import BaseMessage


class ChannelCallbacks:
    """Base class for channel-specific callback hooks.

    Default implementations are no-ops. Channels override the methods they care about.
    Methods that target a user receive `recipient: str` -- not the full context.
    """

    def bind(self, ctx: MessageProcessingContext) -> None:
        """Called after context creation. Override to store a context reference for lazy reads."""

    def transcription_started(self, recipient: str) -> None:
        """Called when voice transcription starts (e.g. show 'uploading voice' indicator)."""

    def transcription_finished(self, recipient: str, transcript: str) -> None:
        """Called when voice transcription completes."""

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        """Send the transcript back to the user."""

    def on_submit_input_to_llm(self, recipient: str) -> None:
        """Called before LLM invocation (e.g. show 'typing' indicator)."""

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        """Retrieve audio content from the inbound message. Must be overridden
        by channels that support voice."""
        raise NotImplementedError("Channel must implement audio retrieval")

    def get_participant_identifier_filter(self, participant_id: str, message: BaseMessage | None) -> Q:
        """Return a Q filter to look up the Participant for this message.

        Override in channel subclasses that need to match on more than one
        identifier (e.g. WhatsApp matches on BSUID OR phone number).
        """
        return Q(identifier=str(participant_id))
