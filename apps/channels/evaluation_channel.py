from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apps.channels.api_channel import NoOpSender
from apps.channels.callbacks import ChannelCallbacks
from apps.channels.capabilities import ChannelCapabilities
from apps.channels.channel_base import ChannelBase
from apps.channels.const import MESSAGE_TYPES
from apps.channels.pipeline import MessageProcessingContext, MessageProcessingPipeline
from apps.channels.stages.core import (
    ChatMessageCreationStage,
    ErrorGuardStage,
    EvalsBotInteractionStage,
    MessageTypeValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
)
from apps.channels.stages.terminal import ActivityTrackingStage
from apps.chat.bots import EvalsBot
from apps.chat.exceptions import ChannelException
from apps.chat.models import ChatMessage, ChatMessageType
from apps.service_providers.tracing import TracingService
from apps.service_providers.tracing.capture_tracer import CaptureTracer

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.channels.sender import ChannelSender
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.service_providers.tracing.base import Tracer
    from apps.service_providers.tracing.capture_tracer import CapturedSpan

logger = logging.getLogger("ocs.channels")


@dataclass(frozen=True)
class EvaluationReply:
    message: ChatMessage
    # Set when the generation failed: `message` is then the pipeline's canned error reply,
    # or empty when the failure escaped the pipeline, not a response the bot generated.
    error: Exception | None = None
    # The bot's final pipeline state; None when no run completed.
    pipeline_state: dict | None = None
    participant_data_diff: list = field(default_factory=list)
    spans: list[CapturedSpan] = field(default_factory=list)


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
        usage_tracer: Tracer | None = None,
    ):
        # Set before super().__init__, which builds the trace service from them.
        self._usage_tracer = usage_tracer
        self._capture_tracer = CaptureTracer()
        super().__init__(experiment, experiment_channel, experiment_session)
        if not self.experiment_session:
            raise ChannelException("EvaluationChannel requires an existing session")
        self._participant_data = participant_data

    def generate_reply(self, message: BaseMessage) -> EvaluationReply:
        """Run `message` through the pipeline and return the reply, with any failure and the calls made before it."""
        try:
            response, ctx = self._process_message(message)
        except Exception as exc:
            logger.exception("Evaluation generation failed for experiment %s", self.experiment.id)
            return EvaluationReply(
                message=ChatMessage(content="", message_type=ChatMessageType.AI),
                error=exc,
                spans=self._capture_tracer.spans,
            )
        bot = ctx.bot if isinstance(ctx.bot, EvalsBot) else None
        return EvaluationReply(
            message=response,
            error=ctx.configuration_error,
            pipeline_state=bot.pipeline_output if bot else None,
            participant_data_diff=bot.participant_data_diff if bot else [],
            spans=self._capture_tracer.spans,
        )

    def _create_trace_service(self):
        """An eval run leaves no `Trace` behind.

        One per evaluated message would flood the team's trace list and outlive the pruned
        eval session. The run gets two tracers that keep none: the usage tracer that bills
        it (ADR-0050) and the capture tracer that records its LLM and tool calls for the
        evaluators (ADR-0050 amendment).
        """
        tracers = [tracer for tracer in (self._usage_tracer, self._capture_tracer) if tracer]
        return TracingService(tracers, self.experiment.id, self.experiment.team_id)

    def _create_context(self, message: BaseMessage) -> MessageProcessingContext:
        ctx = super()._create_context(message)
        ctx.channel_context["participant_data"] = self._participant_data
        return ctx

    def _build_pipeline(self) -> MessageProcessingPipeline:
        # ChannelDisabledStage omitted: the evaluations channel is team-global and has no
        # admin toggle, and an eval run is not participant access to the bot. Honouring a
        # disabled flag here would silently substitute the static message for the bot's
        # output and corrupt the run's results.
        # ParticipantIdentifierStage omitted: the "evaluations" participant is internal and
        # nothing downstream in this pipeline reads ctx.participant_identifier.
        # SessionActivationStage omitted: the eval bot does not gate on session status.
        # PersistenceStage omitted: eval runs never record early-exit text, voice or tags
        # into the eval session's chat.
        return MessageProcessingPipeline(
            core_stages=[
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                ErrorGuardStage(),
                EvalsBotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
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
