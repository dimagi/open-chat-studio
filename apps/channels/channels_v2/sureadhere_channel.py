from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.chat.channels import MESSAGE_TYPES

if TYPE_CHECKING:
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.service_providers.messaging_service import MessagingService


class SureAdhereSender(ChannelSender):
    """Sends messages via the SureAdhere messaging service.

    Text-only sender. Voice and file sending are not supported.
    """

    def __init__(self, messaging_service: MessagingService, tenant_id: str, platform: str):
        self._service = messaging_service
        self._from = tenant_id
        self._platform = platform

    def send_text(self, text: str, recipient: str) -> None:
        self._service.send_text_message(message=text, from_=self._from, to=recipient, platform=self._platform)


class SureAdhereChannel(ChannelBase):
    """SureAdhere channel implementation.

    Text-only channel with no voice or file support. Uses base ChannelCallbacks
    (all no-ops) since SureAdhere has no platform-specific callback behavior.
    """

    voice_replies_supported = False
    supports_multimedia = False
    supported_message_types = (MESSAGE_TYPES.TEXT,)

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
    def tenant_id(self) -> str:
        return self.experiment_channel.extra_data.get("sureadhere_tenant_id", "")

    def _get_sender(self) -> ChannelSender:
        from apps.channels.models import ChannelPlatform  # noqa: PLC0415

        return SureAdhereSender(self.messaging_service, self.tenant_id, ChannelPlatform.SUREADHERE)

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=True,
            supports_static_triggers=True,
            supported_message_types=(MESSAGE_TYPES.TEXT,),
        )
