from __future__ import annotations

from typing import TYPE_CHECKING

from apps.channels.channels_v2.api_channel import NoOpSender
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.pipeline import MessageProcessingContext, MessageProcessingPipeline
from apps.channels.channels_v2.stages.base import ProcessingStage
from apps.channels.channels_v2.stages.core import (
    ChatMessageCreationStage,
    MessageTypeValidationStage,
    ParticipantValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
    SessionActivationStage,
)
from apps.channels.channels_v2.stages.terminal import ActivityTrackingStage, PersistenceStage
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException
from apps.service_providers.tracing import TracingService

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession


class EvalsBotInteractionStage(ProcessingStage):
    """Specialized bot interaction for evaluations.

    Uses EvalsBot instead of get_bot(). Reads participant_data from
    ctx.channel_context (a dict set by EvaluationChannel, not the
    DB-backed ParticipantData model).
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.chat.bots import EvalsBot  # noqa: PLC0415

        participant_data = ctx.channel_context["participant_data"]
        ctx.bot = EvalsBot(
            ctx.experiment_session,
            ctx.experiment,
            ctx.trace_service,
            participant_data=participant_data,
        )
        ctx.bot_response = ctx.bot.process_input(
            ctx.user_query,
            attachments=ctx.message.attachments,
            human_message=ctx.human_message,
        )
        ctx.files_to_send = ctx.bot_response.get_attached_files() or []


class EvaluationChannel(ChannelBase):
    """Message handler for evaluations.

    Uses EvalsBotInteractionStage instead of BotInteractionStage.
    Passes participant_data via ctx.channel_context (workaround --
    see MessageProcessingContext.channel_context).

    No voice, no files, no sending stages. Session is always pre-set.
    Static triggers are disabled.
    """

    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession,
        participant_data: dict,
    ):
        if not experiment_session:
            raise ChannelException("EvaluationChannel requires an existing session")
        self._participant_data = participant_data
        super().__init__(experiment, experiment_channel, experiment_session)

    def _create_trace_service(self):
        return TracingService.empty()

    def _create_context(self, message: BaseMessage) -> MessageProcessingContext:
        ctx = super()._create_context(message)
        ctx.channel_context = {"participant_data": self._participant_data}
        return ctx

    def _get_sender(self) -> NoOpSender:
        return NoOpSender()

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=False,
            supports_static_triggers=False,
            supported_message_types=[MESSAGE_TYPES.TEXT],
        )

    def _build_pipeline(self) -> MessageProcessingPipeline:
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                # No SessionResolutionStage -- session always pre-set
                SessionActivationStage(),
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                EvalsBotInteractionStage(),  # Instead of BotInteractionStage
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                # No sending stages -- evaluations are internal
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )

    @property
    def participant_user(self):
        if self.experiment_session:
            return self.experiment_session.participant.user
        return None
