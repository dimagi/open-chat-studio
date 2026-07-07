from __future__ import annotations

from typing import TYPE_CHECKING

from apps.channels.callbacks import ChannelCallbacks
from apps.channels.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.models import ChannelPlatform
from apps.channels.sender import ChannelSender

if TYPE_CHECKING:
    from io import BytesIO

    from apps.channels.channels_v2.pipeline import MessageProcessingContext
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.service_providers.messaging_service import MessagingService
    from apps.service_providers.speech_service import SynthesizedAudio


class FacebookMessengerSender(ChannelSender):
    """Delivers messages over Facebook Messenger via the configured messaging service.

    Messenger supports text and voice replies; file sending is not supported,
    so send_file keeps the base NotImplementedError.
    """

    def __init__(self, service: MessagingService, page_id: str) -> None:
        self._service = service
        self._page_id = page_id
        self._ctx: MessageProcessingContext | None = None

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    @property
    def _last_activity_at(self):
        return self._ctx.last_activity_at if self._ctx else None

    def send_text(self, text: str, recipient: str) -> None:
        self._service.send_text_message(
            message=text,
            from_=self._page_id,
            to=recipient,
            platform=ChannelPlatform.FACEBOOK,
            last_activity_at=self._last_activity_at,
        )

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        self._service.send_voice_message(
            audio,
            from_=self._page_id,
            to=recipient,
            platform=ChannelPlatform.FACEBOOK,
            last_activity_at=self._last_activity_at,
        )


class FacebookMessengerCallbacks(ChannelCallbacks):
    """Facebook Messenger callbacks: transcript echo and audio retrieval."""

    def __init__(self, service: MessagingService, page_id: str) -> None:
        self._service = service
        self._page_id = page_id
        self._ctx: MessageProcessingContext | None = None

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        self._service.send_text_message(
            message=f'I heard: "{transcript}"',
            from_=self._page_id,
            to=recipient,
            platform=ChannelPlatform.FACEBOOK,
            last_activity_at=self._ctx.last_activity_at if self._ctx else None,
        )

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        return self._service.get_message_audio(message)


class FacebookMessengerChannel(ChannelBase):
    """Facebook Messenger channel backed by a Twilio messaging service.

    Voice support and supported message types come from the messaging service.
    Outbound files are not supported -- they fall back to download links.
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
    def _page_id(self) -> str:
        page_id = self.experiment_channel.extra_data.get("page_id")
        if not page_id:
            raise ValueError("Facebook channel is missing page_id in extra_data")
        return page_id

    def _get_sender(self) -> FacebookMessengerSender:
        return FacebookMessengerSender(service=self.messaging_service, page_id=self._page_id)

    def _get_callbacks(self) -> FacebookMessengerCallbacks:
        return FacebookMessengerCallbacks(service=self.messaging_service, page_id=self._page_id)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.messaging_service.voice_replies_supported,
            supports_files=False,
            supports_conversational_consent=True,
            supported_message_types=tuple(self.messaging_service.supported_message_types),
        )
