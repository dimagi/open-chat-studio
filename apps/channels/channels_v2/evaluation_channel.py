from __future__ import annotations

from typing import TYPE_CHECKING

from apps.channels.capabilities import ChannelCapabilities
from apps.channels.channels_v2.api_channel import NoOpSender
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.pipeline import MessageProcessingContext, MessageProcessingPipeline
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.channels_v2.stages.core import (
    ChatMessageCreationStage,
    EvalsBotInteractionStage,
    MessageTypeValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
)
from apps.channels.channels_v2.stages.terminal import ActivityTrackingStage, PersistenceStage
from apps.channels.const import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException
from apps.service_providers.tracing import TracingService

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession


class EvaluationChannel(ChannelBase):
    """Message handler for evaluation runs.

    Internal channel — no message sending. Uses EvalsBot with in-memory
    participant_data passed via ctx.channel_context (see
    MessageProcessingContext.channel_context for the workaround note).
    """

    voice_replies_supported = False
    supported_message_types = (MESSAGE_TYPES.TEXT,)

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession,
        participant_data: dict,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        if not self.experiment_session:
            raise ChannelException("EvaluationChannel requires an existing session")
        self._participant_data = participant_data

    def _create_trace_service(self):
        return TracingService.empty()

    def _create_context(self, message: BaseMessage) -> MessageProcessingContext:
        ctx = super()._create_context(message)
        ctx.channel_context["participant_data"] = self._participant_data
        return ctx

    def _build_pipeline(self) -> MessageProcessingPipeline:
        # ParticipantValidationStage omitted: the "evaluations" participant is internal,
        # validating against participant_allowlist is meaningless (and would block private
        # experiments). Nothing downstream in this pipeline reads ctx.participant_identifier.
        # SessionActivationStage omitted: the eval bot does not gate on session status.
        return MessageProcessingPipeline(
            core_stages=[
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                EvalsBotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_sender(self) -> ChannelSender:
        return NoOpSender()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=False,
            supports_conversational_consent=False,
            supports_static_triggers=False,
            supported_message_types=self.supported_message_types,
        )
