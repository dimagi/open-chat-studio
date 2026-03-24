from __future__ import annotations

from functools import cached_property
from io import BytesIO
from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.messaging_service import MessagingService
    from apps.service_providers.speech_service import SynthesizedAudio


class WhatsappSender(ChannelSender):
    """Sends messages via a WhatsApp messaging service (Twilio, Turn.io, or Meta Cloud API).

    Delegates all sending to the messaging service. The `from_identifier` and
    platform are baked in at construction time.
    """

    def __init__(self, messaging_service: MessagingService, from_identifier: str, platform: str):
        self._service = messaging_service
        self._from = from_identifier
        self._platform = platform

    def send_text(self, text: str, recipient: str) -> None:
        self._service.send_text_message(message=text, from_=self._from, to=recipient, platform=self._platform)

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        self._service.send_voice_message(audio, from_=self._from, to=recipient, platform=self._platform)

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        self._service.send_file_to_user(
            from_=self._from,
            to=recipient,
            platform=self._platform,
            file=file,
            download_link=file.download_link(experiment_session_id=session_id),
        )


class WhatsappCallbacks(ChannelCallbacks):
    """WhatsApp-specific callbacks: transcript echo and audio retrieval."""

    def __init__(self, sender: WhatsappSender, messaging_service: MessagingService):
        self._sender = sender
        self._service = messaging_service

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        self._sender.send_text(text=f'I heard: "{transcript}"', recipient=recipient)

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        return self._service.get_message_audio(message=message)


class WhatsappChannel(ChannelBase):
    """WhatsApp channel implementation.

    Capabilities are resolved at runtime from the messaging service, since
    different providers (Twilio, Turn.io, Meta Cloud API) have different
    capabilities.
    """

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)

    @cached_property
    def messaging_service(self) -> MessagingService:
        return self.experiment_channel.messaging_provider.get_messaging_service()

    @property
    def from_identifier(self) -> str:
        from apps.service_providers.models import MessagingProviderType  # noqa: PLC0415

        extra_data = self.experiment_channel.extra_data
        if self.experiment_channel.messaging_provider.type == MessagingProviderType.meta_cloud_api:
            phone_number_id = extra_data.get("phone_number_id")
            if not phone_number_id:
                raise ValueError("Meta Cloud API channel is missing phone_number_id in extra_data")
            return phone_number_id
        return extra_data["number"]

    @property
    def voice_replies_supported(self) -> bool:
        return self.messaging_service.voice_replies_supported

    @property
    def supports_multimedia(self) -> bool:
        return getattr(self.messaging_service, "supports_multimedia", False)

    @property
    def supported_message_types(self) -> list:
        return self.messaging_service.supported_message_types

    def _get_sender(self) -> ChannelSender:
        from apps.channels.models import ChannelPlatform  # noqa: PLC0415

        return WhatsappSender(self.messaging_service, self.from_identifier, ChannelPlatform.WHATSAPP)

    def _get_callbacks(self) -> ChannelCallbacks:
        from apps.channels.models import ChannelPlatform  # noqa: PLC0415

        sender = WhatsappSender(self.messaging_service, self.from_identifier, ChannelPlatform.WHATSAPP)
        return WhatsappCallbacks(sender=sender, messaging_service=self.messaging_service)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=self.supports_multimedia,
            supports_conversational_consent=True,
            supports_static_triggers=True,
            supported_message_types=self.supported_message_types,
            can_send_file=self._can_send_file,
        )

    def _can_send_file(self, file: File) -> bool:
        if hasattr(self.messaging_service, "can_send_file"):
            return self.messaging_service.can_send_file(file)  # ty: ignore[call-non-callable]
        return False
