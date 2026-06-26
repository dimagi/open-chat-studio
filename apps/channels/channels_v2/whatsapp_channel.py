from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.channels_v2.stages.core import AttachmentHydrationStage
from apps.channels.datamodels import WhatsAppMessage
from apps.channels.models import ChannelPlatform
from apps.files.models import File, FilePurpose
from apps.service_providers.models import MessagingProviderType

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.service_providers.messaging_service import MessagingService
    from apps.service_providers.speech_service import SynthesizedAudio

logger = logging.getLogger("ocs.channels")


def _resolve_recipient(ctx: MessageProcessingContext | None, fallback: str) -> str:
    """Return the WhatsApp send recipient: the phone number stored on the participant's
    remote_id if we have one, else the given identifier. The participant identifier may be a
    BSUID, which Meta/Twilio cannot yet accept as an outbound recipient, so we prefer the phone
    captured on inbound."""
    if ctx is not None and ctx.participant is not None and ctx.participant.remote_id:
        return ctx.participant.remote_id
    return fallback


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
            to=_resolve_recipient(self._ctx, recipient),
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=self._last_activity_at,
        )

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        self._service.send_voice_message(
            audio,
            from_=self._from,
            to=_resolve_recipient(self._ctx, recipient),
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=self._last_activity_at,
        )

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        self._service.send_file_to_user(
            from_=self._from,
            to=_resolve_recipient(self._ctx, recipient),
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
            to=_resolve_recipient(self._ctx, recipient),
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=last_activity_at,
        )

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        return self._service.get_message_audio(message)

    def on_submit_input_to_llm(self, recipient: str) -> None:
        if self._ctx is None:
            return
        message = self._ctx.message
        if not isinstance(message, WhatsAppMessage) or not message.whatsapp_message_id:
            return
        try:
            self._service.send_typing_indicator(
                from_=self._from,
                message_id=message.whatsapp_message_id,
            )
        except Exception:
            logger.exception("Failed to send typing indicator")


class WhatsappAttachmentHydrationStage(AttachmentHydrationStage):
    """Download, persist, and hydrate inbound WhatsApp media attachments
    (images, documents, and other non-voice media).

    should_run fires only when the message references downloadable media —
    WhatsAppMessage.parse sets attachment_mime_type on every parsed message
    (including text), so the mime type alone isn't a reliable gate.
    _get_files() downloads the media via the messaging service and persists
    the bytes as a MESSAGE_MEDIA File. The base class then handles
    ChatAttachment linkage and Attachment construction. Size and
    content-type policing is the upstream provider's responsibility —
    Meta already caps what reaches us.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        if not ctx.experiment_session or not ctx.message:
            return False
        if ctx.message.attachments:
            return False
        if not ctx.message.attachment_mime_type:
            return False
        return bool(getattr(ctx.message, "media_url", None) or getattr(ctx.message, "media_id", None))

    def _get_files(self, ctx: MessageProcessingContext) -> list[File]:
        # messaging_provider is nullable on ExperimentChannel; a WhatsApp channel
        # without a provider can't fetch media, so skip rather than AttributeError.
        messaging_provider = ctx.experiment_channel.messaging_provider
        if messaging_provider is None:
            logger.warning("WhatsApp channel has no messaging provider; cannot download inbound media")
            return []
        messaging_service = messaging_provider.get_messaging_service()

        try:
            media = messaging_service.get_inbound_media(ctx.message)
        except Exception:
            logger.exception("Failed to download WhatsApp inbound media")
            return []

        if media is None:
            return []

        raw_bytes, content_type = media
        filename = self._resolve_filename(ctx, content_type)

        try:
            file = File.create(
                filename=filename,
                file_obj=BytesIO(raw_bytes),
                team_id=ctx.experiment.team_id,
                purpose=FilePurpose.MESSAGE_MEDIA,
                content_type=content_type,
            )
        except Exception:
            logger.exception("Failed to persist WhatsApp inbound media")
            return []

        return [file]

    @staticmethod
    def _resolve_filename(ctx: MessageProcessingContext, content_type: str) -> str:
        """Prefer the provider-supplied filename (documents). Fall back to a
        family-based name when the provider doesn't send one (e.g. images)."""
        provided = getattr(ctx.message, "attachment_filename", None)
        if provided:
            return provided
        family = content_type.split("/", 1)[0] if "/" in content_type else "attachment"
        return family or "attachment"


class WhatsappChannel(ChannelBase):
    """WhatsApp channel supporting Twilio, TurnIO, and Meta Cloud API providers.

    Capabilities (voice, multimedia, supported message types) are determined
    at runtime from the messaging service, since they vary by provider.
    """

    attachment_hydration_stage_class = WhatsappAttachmentHydrationStage

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
