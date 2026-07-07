from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.const import MESSAGE_TYPES
from apps.channels.datamodels import SlackMessage
from apps.channels.models import ChannelPlatform
from apps.channels.sender import ChannelSender
from apps.chat.exceptions import ChannelException
from apps.service_providers.file_limits import can_send_on_slack
from apps.slack.utils import parse_session_external_id

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.messaging_service import SlackService

logger = logging.getLogger("ocs.channels")


class SlackSender(ChannelSender):
    """Delivers messages to a Slack channel + thread via the messaging service.

    Slack sends are scoped to a channel + thread rather than the participant
    identifier. bind(ctx) is called after context creation; the channel_id and
    thread_ts resolve from the inbound message, falling back to the session's
    external_id for ad hoc sends where there is no inbound message.
    """

    def __init__(self, service: SlackService) -> None:
        self._service = service
        self._ctx: MessageProcessingContext | None = None

    def bind(self, ctx: MessageProcessingContext) -> None:
        self._ctx = ctx

    def _resolve_destination(self) -> tuple[str, str]:
        """Return the (channel_id, thread_ts) to send to."""
        if self._ctx is None:
            raise ChannelException("SlackSender used before bind()")
        if isinstance(self._ctx.message, SlackMessage):
            return self._ctx.message.channel_id, self._ctx.message.thread_ts
        return parse_session_external_id(self._ctx.experiment_session.external_id)

    def send_text(self, text: str, recipient: str) -> None:
        channel_id, thread_ts = self._resolve_destination()
        self._service.send_text_message(
            text,
            from_="",
            to=channel_id,
            platform=ChannelPlatform.SLACK,
            thread_ts=thread_ts,
        )

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        channel_id, thread_ts = self._resolve_destination()
        self._service.send_file_message(
            file=file,
            to=channel_id,
            thread_ts=thread_ts,
        )


class SlackChannel(ChannelBase):
    """Message handler for Slack.

    Sessions map to Slack threads and are always pre-set (created by the
    Slack event listener before the pipeline runs), so SessionResolutionStage
    is a no-op. That also means /reset is not intercepted and reaches the bot
    as a normal message — reset never worked on Slack; starting a new thread
    starts a new session. Text-only inbound; supports file replies, no voice.
    """

    supports_multimedia = True
    supported_message_types = (MESSAGE_TYPES.TEXT,)

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
        messaging_service: SlackService | None = None,
    ):
        if not experiment_session:
            raise ChannelException("SlackChannel requires an existing session")
        super().__init__(experiment, experiment_channel, experiment_session)
        self._messaging_service = messaging_service

    @property
    def messaging_service(self) -> SlackService:
        if not self._messaging_service:
            self._messaging_service = self.experiment_channel.messaging_provider.get_messaging_service()
        return self._messaging_service

    def _get_sender(self) -> SlackSender:
        return SlackSender(service=self.messaging_service)

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _can_send_file(self, file: File) -> bool:
        return can_send_on_slack(file.content_type or "", file.content_size or 0).supported
