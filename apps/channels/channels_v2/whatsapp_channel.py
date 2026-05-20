from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProviderType

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.messaging_service import MessagingService
    from apps.service_providers.speech_service import SynthesizedAudio

logger = logging.getLogger("ocs.channels")


class WhatsappSender(ChannelSender):
    """Delivers messages over WhatsApp via the configured messaging service.

    bind(ctx) is called by ChannelBase after context creation; _last_activity_at
    then lazily resolves from ctx.experiment_session for MetaCloudAPI's 24-hour
    service window check.
    """

    def __init__(self, service: MessagingService, from_number: str) -> None:
        self._service = service
        self._from = from_number
        self._ctx: MessageProcessingContext | None = None

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    @property
    def _last_activity_at(self):
        return self._ctx.last_activity_at if self._ctx else None

    def send_text(self, text: str, recipient: str) -> None:
        self._service.send_text_message(
            message=text,
            from_=self._from,
            to=recipient,
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=self._last_activity_at,
        )

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        self._service.send_voice_message(
            audio,
            from_=self._from,
            to=recipient,
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=self._last_activity_at,
        )

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        self._service.send_file_to_user(
            from_=self._from,
            to=recipient,
            platform=ChannelPlatform.WHATSAPP,
            file=file,
            download_link=file.download_link(experiment_session_id=session_id),
            last_activity_at=self._last_activity_at,
        )


class WhatsappCallbacks(ChannelCallbacks):
    """WhatsApp-specific callbacks: transcript echo, audio retrieval, and typing indicator."""

    def __init__(self, service: MessagingService, from_number: str) -> None:
        self._service = service
        self._from = from_number
        self._ctx: MessageProcessingContext | None = None

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        last_activity_at = self._ctx.last_activity_at if self._ctx else None
        self._service.send_text_message(
            message=f'I heard: "{transcript}"',
            from_=self._from,
            to=recipient,
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=last_activity_at,
        )

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        return self._service.get_message_audio(message)

    def submit_input_to_llm(self, recipient: str) -> None:
        # noqa: PLC0415 - circular: datamodels imports chat.channels
        from apps.channels.datamodels import MetaCloudAPIMessage  # noqa: PLC0415

        if self._ctx is None:
            return
        message = self._ctx.message
        if not isinstance(message, MetaCloudAPIMessage) or not message.whatsapp_message_id:
            return
        try:
            self._service.send_typing_indicator(
                from_=self._from,
                message_id=message.whatsapp_message_id,
            )
        except Exception:
            logger.exception("Failed to send typing indicator")


class WhatsappChannel(ChannelBase):
    """WhatsApp channel supporting Twilio, TurnIO, and Meta Cloud API providers.

    Capabilities (voice, multimedia, supported message types) are determined
    at runtime from the messaging service, since they vary by provider.
    """

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ) -> None:
        super().__init__(experiment, experiment_channel, experiment_session)
        self._messaging_service_cache: MessagingService | None = None

    @property
    def messaging_service(self) -> MessagingService:
        if self._messaging_service_cache is None:
            self._messaging_service_cache = self.experiment_channel.messaging_provider.get_messaging_service()
        return self._messaging_service_cache

    @property
    def _from_identifier(self) -> str:
        extra_data = self.experiment_channel.extra_data
        if self.experiment_channel.messaging_provider.type == MessagingProviderType.meta_cloud_api:
            phone_number_id = extra_data.get("phone_number_id")
            if not phone_number_id:
                raise ValueError("Meta Cloud API channel is missing phone_number_id in extra_data")
            return phone_number_id
        return extra_data["number"]

    def _get_sender(self) -> WhatsappSender:
        return WhatsappSender(service=self.messaging_service, from_number=self._from_identifier)

    def _get_callbacks(self) -> WhatsappCallbacks:
        return WhatsappCallbacks(service=self.messaging_service, from_number=self._from_identifier)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.messaging_service.voice_replies_supported,
            supports_files=self.messaging_service.supports_multimedia,
            supports_conversational_consent=True,
            supported_message_types=tuple(self.messaging_service.supported_message_types),
            can_send_file=self.messaging_service.can_send_file,
        )
