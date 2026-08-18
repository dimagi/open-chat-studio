from __future__ import annotations

from typing import TYPE_CHECKING

from apps.channels.api_channel import NoOpSender
from apps.channels.callbacks import ChannelCallbacks
from apps.channels.capabilities import ChannelCapabilities
from apps.channels.channel_base import ChannelBase
from apps.channels.const import MESSAGE_TYPES
from apps.channels.models import ExperimentChannel
from apps.channels.pipeline import MessageProcessingPipeline
from apps.channels.stages.core import (
    BotInteractionStage,
    ChannelDisabledStage,
    ChatMessageCreationStage,
    MessageTypeValidationStage,
    ParticipantValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
    SessionActivationStage,
)
from apps.channels.stages.terminal import ActivityTrackingStage, PersistenceStage
from apps.chat.exceptions import ChannelException
from apps.experiments.models import Experiment

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession


class WebChannel(ChannelBase):
    """Message handler for browser-driven chat.

    No message sending, no conversational consent: the response is returned by
    new_user_message() and handed back to the caller (see
    `apps.experiments.tasks.get_response_for_webchat_task`, which the chat API uses).
    The session is always pre-set — this channel never creates one.
    """

    supported_message_types = [MESSAGE_TYPES.TEXT]

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        if not experiment_session:
            raise ChannelException("WebChannel requires an existing session")
        super().__init__(experiment, experiment_channel, experiment_session)

    def _get_sender(self) -> NoOpSender:
        return NoOpSender()

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=self.supports_multimedia,
            supports_conversational_consent=False,
            supported_message_types=self.supported_message_types,
        )

    def _build_pipeline(self) -> MessageProcessingPipeline:
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                # Embedded-widget sessions are served here, not by ApiChannel, so the
                # admin's channel toggle has to be enforced on this pipeline too.
                ChannelDisabledStage(),
                # No SessionResolutionStage — session always pre-set
                SessionActivationStage(),
                MessageTypeValidationStage(),
                # No ConsentFlowStage — the consent form UI that collected it was removed
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                # No ResponseSendingStage or SendingErrorHandlerStage —
                # responses returned via new_user_message()
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )
