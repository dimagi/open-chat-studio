from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.channels.capabilities import ChannelCapabilities
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.models import ChannelPlatform
from apps.channels.sender import ChannelSender

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.service_providers.messaging_service import MessagingService

logger = logging.getLogger("ocs.channels")


class SureAdhereSender(ChannelSender):
    """Delivers text messages over SureAdhere via the configured messaging service.

    SureAdhere is text-only: voice and file sending are not supported, so only
    send_text is overridden.
    """

    def __init__(self, service: MessagingService, tenant_id: str) -> None:
        self._service = service
        self._tenant_id = tenant_id
        self._ctx: MessageProcessingContext | None = None

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    def send_text(self, text: str, recipient: str) -> None:
        self._service.send_text_message(
            message=text,
            from_=self._tenant_id,
            to=recipient,
            platform=ChannelPlatform.SUREADHERE,
            last_activity_at=self._ctx.last_activity_at if self._ctx else None,
        )


class SureAdhereChannel(ChannelBase):
    """Text-only channel backed by the SureAdhere messaging service.

    Capabilities (supported message types) are read from the messaging service,
    which is text-only. No voice, multimedia, or file support.
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
    def _tenant_id(self) -> str:
        return self.experiment_channel.extra_data.get("sureadhere_tenant_id")

    def _get_sender(self) -> SureAdhereSender:
        return SureAdhereSender(service=self.messaging_service, tenant_id=self._tenant_id)

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.messaging_service.voice_replies_supported,
            supports_files=self.messaging_service.supports_multimedia,
            supports_conversational_consent=True,
            supported_message_types=tuple(self.messaging_service.supported_message_types),
        )
