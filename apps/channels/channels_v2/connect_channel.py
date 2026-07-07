from __future__ import annotations

import logging

from apps.channels.capabilities import ChannelCapabilities, PlatformConsentConfig
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.pipeline import MessageProcessingContext
from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.const import MESSAGE_TYPES
from apps.channels.sender import ChannelSender
from apps.chat.exceptions import ChannelException

logger = logging.getLogger("ocs.channels")


class CommCareConnectSender(ChannelSender):
    """Delivers messages to CommCare Connect users via encrypted FCM messages.

    Reads ``ctx.participant_data`` -- a cached_property on the context that
    fetches lazily on first access (and is typically populated during
    ConsentCheckStage in the normal pipeline).
    """

    def __init__(self) -> None:
        self._ctx: MessageProcessingContext | None = None

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    def send_text(self, text: str, recipient: str) -> None:
        if self._ctx is None:
            # Runtime guard rather than assert: asserts are stripped under
            # `python -O` and would degrade to an AttributeError below.
            raise ChannelException("CommCareConnectSender must be bound to a context before sending")

        participant_data = self._ctx.participant_data
        if participant_data is None:
            raise ChannelException(f"Participant data not found for participant {recipient}")

        channel_id = participant_data.system_metadata.get("commcare_connect_channel_id")
        if not channel_id:
            raise ChannelException(f"channel_id is missing for participant {recipient}")

        if not participant_data.encryption_key:
            # Generate a key on the fly when one is missing. The mobile app
            # always calls `get_key` before attempting to decrypt, so it will
            # pick up whatever key we use to encrypt this message. See PR #1326.
            participant_data.generate_encryption_key()

        CommCareConnectClient().send_message_to_user(
            channel_id=channel_id,
            message=text,
            encryption_key=participant_data.get_encryption_key_bytes(),
        )


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

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_sender(self) -> ChannelSender:
        return CommCareConnectSender()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=self.supports_multimedia,
            supports_conversational_consent=True,
            supported_message_types=self.supported_message_types,
            can_send_file=self._can_send_file,
            # Strict consent: a participant must have ParticipantData with
            # consent=True. Matches the v1 channel's _check_consent() behavior.
            consent_config=PlatformConsentConfig(strict=True, default_consent=False),
        )
