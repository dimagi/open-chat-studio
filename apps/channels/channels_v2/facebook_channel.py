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
    from apps.service_providers.messaging_service import MessagingService
    from apps.service_providers.speech_service import SynthesizedAudio


class FacebookSender(ChannelSender):
    """Sends messages via a Facebook Messenger messaging service.

    Delegates all sending to the messaging service. The `page_id` and
    platform are baked in at construction time.
    """

    def __init__(self, messaging_service: MessagingService, page_id: str, platform: str):
        self._service = messaging_service
        self._from = page_id
        self._platform = platform

    def send_text(self, text: str, recipient: str) -> None:
        self._service.send_text_message(message=text, from_=self._from, to=recipient, platform=self._platform)

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        self._service.send_voice_message(audio, from_=self._from, to=recipient, platform=self._platform)


class FacebookCallbacks(ChannelCallbacks):
    """Facebook Messenger-specific callbacks: transcript echo and audio retrieval."""

    def __init__(self, sender: FacebookSender, messaging_service: MessagingService):
        self._sender = sender
        self._service = messaging_service

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        self._sender.send_text(text=f'I heard: "{transcript}"', recipient=recipient)

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        return self._service.get_message_audio(message=message)


class FacebookMessengerChannel(ChannelBase):
    """Facebook Messenger channel implementation.

    Capabilities are resolved at runtime from the messaging service.
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
    def page_id(self) -> str:
        return self.experiment_channel.extra_data.get("page_id", "")

    @property
    def voice_replies_supported(self) -> bool:
        return self.messaging_service.voice_replies_supported

    @property
    def supported_message_types(self) -> list:
        return self.messaging_service.supported_message_types

    def _get_sender(self) -> ChannelSender:
        from apps.channels.models import ChannelPlatform  # noqa: PLC0415

        return FacebookSender(self.messaging_service, self.page_id, ChannelPlatform.FACEBOOK)

    def _get_callbacks(self) -> ChannelCallbacks:
        from apps.channels.models import ChannelPlatform  # noqa: PLC0415

        sender = FacebookSender(self.messaging_service, self.page_id, ChannelPlatform.FACEBOOK)
        return FacebookCallbacks(sender=sender, messaging_service=self.messaging_service)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=False,
            supports_conversational_consent=True,
            supports_static_triggers=True,
            supported_message_types=self.supported_message_types,
        )
