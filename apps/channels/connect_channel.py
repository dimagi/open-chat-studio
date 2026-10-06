from __future__ import annotations

import logging
import os
from functools import cached_property
from typing import TYPE_CHECKING

from apps.channels.callbacks import ChannelCallbacks
from apps.channels.capabilities import ChannelCapabilities, PlatformConsentConfig
from apps.channels.channel_base import ChannelBase
from apps.channels.clients.connect_client import (
    ATTACHMENT_ENCRYPTION_OVERHEAD_BYTES,
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_MESSAGE_ATTACHMENT_BYTES,
    CommCareConnectClient,
    OutgoingAttachment,
    fits_attachment_limit,
)
from apps.channels.const import MESSAGE_TYPES
from apps.channels.sender import ChannelSender
from apps.chat.exceptions import ChannelException
from apps.teams.flags import Flags
from apps.teams.utils import flag_is_active_for_team

if TYPE_CHECKING:
    from collections.abc import Iterable

    from apps.channels.pipeline import MessageProcessingContext
    from apps.files.models import File

logger = logging.getLogger("ocs.channels")

# PersonalID's limit on an attachment name, in characters
_MAX_ATTACHMENT_NAME_LENGTH = 255


def unique_attachment_name(name: str, taken: Iterable[str]) -> str:
    """The file's base name, made unique (ignoring case) among ``taken`` and at most 255 characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if base in ("", ".", ".."):
        base = "attachment"
    stem, extension = os.path.splitext(base)
    taken_names = {existing.casefold() for existing in taken}

    candidate = _fit_attachment_name(stem, extension, counter="")
    copy_number = 2
    while candidate.casefold() in taken_names:
        candidate = _fit_attachment_name(stem, extension, counter=f" ({copy_number})")
        copy_number += 1
    return candidate


def _fit_attachment_name(stem: str, extension: str, counter: str) -> str:
    """Join the parts, shortening the stem so the name fits PersonalID's limit."""
    room = _MAX_ATTACHMENT_NAME_LENGTH - len(counter) - len(extension)
    if room < 1:
        # An "extension" too long to leave room for the stem is part of the name
        stem, extension = stem + extension, ""
        room = _MAX_ATTACHMENT_NAME_LENGTH - len(counter)
    return stem[:room] + counter + extension


def attachment_mime_type(content_type: str | None) -> str:
    """The bare, lowercase MIME type PersonalID records for an attachment."""
    mime_type = (content_type or "").split(";", 1)[0].strip().lower()
    return mime_type or "application/octet-stream"


class CommCareConnectSender(ChannelSender):
    """Delivers messages to CommCare Connect users through PersonalID.

    Buffers the text and files of a reply and sends them as one encrypted message in flush().
    A file PersonalID cannot take in this message is sent as a download link in its text
    instead. With ``send_fcm_fallback`` the reply goes through the legacy send_fcm endpoint,
    which carries text only.

    Reads ``ctx.participant_data``, which ParticipantResolverStage sets.
    """

    def __init__(self, send_fcm_fallback: bool = False) -> None:
        self.send_fcm_fallback = send_fcm_fallback
        self._ctx: MessageProcessingContext | None = None
        self._recipient = ""
        self._text = ""
        self._attachments: list[OutgoingAttachment] = []
        self._attachment_bytes = 0

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    def send_text(self, text: str, recipient: str) -> None:
        """Buffer the text. Nothing is sent until flush()."""
        self._recipient = recipient
        self._text = f"{self._text}\n\n{text}" if self._text else text

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        """Buffer the file as an attachment, or its download link as text when it cannot be attached."""
        self._recipient = recipient
        if reason := self._reason_to_link_unread_file():
            self._send_as_link(file, recipient, session_id, reason)
            return

        content = file.read_bytes()
        if reason := self._reason_to_link_file_of_size(len(content)):
            self._send_as_link(file, recipient, session_id, reason)
            return

        self._attachments.append(
            OutgoingAttachment(
                name=unique_attachment_name(file.name, [attachment.name for attachment in self._attachments]),
                content_type=attachment_mime_type(file.content_type),
                content=content,
            )
        )
        self._attachment_bytes += len(content) + ATTACHMENT_ENCRYPTION_OVERHEAD_BYTES

    def flush(self) -> None:
        """Send the buffered text and attachments as one message, then reset the buffer."""
        if not self._text and not self._attachments:
            return

        try:
            self._send_buffered()
        finally:
            # Reset even when the send failed, so a later flush never resends this reply
            self._recipient = ""
            self._text = ""
            self._attachments = []
            self._attachment_bytes = 0

    def _reason_to_link_unread_file(self) -> str | None:
        """Why the next file cannot be attached whatever its size, so it need not be read."""
        if self.send_fcm_fallback:
            return "the send_fcm fallback is on"
        if len(self._attachments) >= MAX_ATTACHMENTS_PER_MESSAGE:
            return f"the message already has {MAX_ATTACHMENTS_PER_MESSAGE} attachments"
        return None

    def _reason_to_link_file_of_size(self, content_size: int) -> str | None:
        if content_size == 0:
            return "the file is empty"
        if not fits_attachment_limit(content_size):
            return "the file is over the attachment size limit"
        encrypted_size = content_size + ATTACHMENT_ENCRYPTION_OVERHEAD_BYTES
        if self._attachment_bytes + encrypted_size > MAX_MESSAGE_ATTACHMENT_BYTES:
            return "the message's attachments would be over the size limit"
        return None

    def _send_as_link(self, file: File, recipient: str, session_id: int, reason: str) -> None:
        logger.warning("Sending file %s to CommCare Connect as a download link: %s", file.id, reason)
        self.send_text(f"{file.name}\n{file.download_link(session_id)}", recipient)

    def _send_buffered(self) -> None:
        channel_id, encryption_key = self._channel_id_and_encryption_key()
        client = CommCareConnectClient()
        if self.send_fcm_fallback:
            client.send_message_to_user(channel_id=channel_id, message=self._text, encryption_key=encryption_key)
        else:
            client.create_message(
                channel_id=channel_id,
                encryption_key=encryption_key,
                text=self._text,
                attachments=self._attachments,
            )

    def _channel_id_and_encryption_key(self) -> tuple[str, bytes]:
        if self._ctx is None:
            # Runtime guard rather than assert: asserts are stripped under
            # `python -O` and would degrade to an AttributeError below.
            raise ChannelException("CommCareConnectSender must be bound to a context before sending")

        participant_data = self._ctx.participant_data
        if participant_data is None:
            raise ChannelException(f"Participant data not found for participant {self._recipient}")

        channel_id = participant_data.system_metadata.get("commcare_connect_channel_id")
        if not channel_id:
            raise ChannelException(f"channel_id is missing for participant {self._recipient}")

        if not participant_data.encryption_key:
            # Generate a key on the fly when one is missing. The mobile app
            # always calls `get_key` before attempting to decrypt, so it will
            # pick up whatever key we use to encrypt this message. See PR #1326.
            participant_data.generate_encryption_key()

        return channel_id, participant_data.get_encryption_key_bytes()


class CommCareConnectChannel(ChannelBase):
    """CommCare Connect channel.

    Uses the base pipeline as-is. Platform-level consent is enforced by the
    generic ConsentCheckStage, configured via ``_get_capabilities()`` to
    require explicit ParticipantData with ``consent=True``.

    For local development and testing, ``scripts/mock_connect_server.py``
    runs a local HTTP server that impersonates the Connect backend and handles
    the full key-negotiation + message send/receive flow.
    """

    supported_message_types = (MESSAGE_TYPES.TEXT,)

    @cached_property
    def _send_fcm_fallback(self) -> bool:
        """Read once, so the capabilities and the sender of one message always agree."""
        return flag_is_active_for_team(self.experiment.team, Flags.COMMCARE_CONNECT_SEND_FCM_FALLBACK.slug)

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_sender(self) -> ChannelSender:
        return CommCareConnectSender(send_fcm_fallback=self._send_fcm_fallback)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=not self._send_fcm_fallback,
            supports_conversational_consent=True,
            supported_message_types=self.supported_message_types,
            can_send_file=self._can_send_file,
            # Strict consent: a participant must have ParticipantData with
            # consent=True. Matches the v1 channel's _check_consent() behavior.
            consent_config=PlatformConsentConfig(strict=True, default_consent=False),
        )

    def _can_send_file(self, file: File) -> bool:
        return fits_attachment_limit(file.content_size)
