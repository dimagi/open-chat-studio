from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.pipeline import MessageProcessingContext, MessageProcessingPipeline
from apps.channels.channels_v2.stages.core import (
    BotInteractionStage,
    ChatMessageCreationStage,
    ConsentFlowStage,
    MessageTypeValidationStage,
    ParticipantValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
    SessionActivationStage,
    SessionResolutionStage,
)
from apps.channels.channels_v2.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.chat.models import ChatMessage, ChatMessageType
from apps.service_providers.llm_service.runnables import GenerationCancelled
from apps.service_providers.tracing import TracingService
from apps.teams.utils import current_team

if TYPE_CHECKING:
    from apps.channels.channels_v2.callbacks import ChannelCallbacks
    from apps.channels.channels_v2.sender import ChannelSender
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession

logger = logging.getLogger("ocs.channels")


class ChannelBase(ABC):
    """Base channel -- builds the pipeline and provides the entry point.

    Channels are now "pipeline builders + callback/sender providers."
    The heavy processing logic lives in the stages.
    """

    # Class-level defaults (overridden by subclasses)
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types: ClassVar[list] = []

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        self.experiment = experiment
        self.experiment_channel = experiment_channel
        self.experiment_session = experiment_session
        self.trace_service = self._create_trace_service()

    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        """Main entry point -- runs a message through the processing pipeline."""
        with current_team(self.experiment.team):
            ctx = self._create_context(message)
            pipeline = self._build_pipeline()

            try:
                with self.trace_service.trace(
                    trace_name=self.experiment.name,
                    session=ctx.experiment_session,
                    inputs={"input": message.model_dump()},
                ) as span:
                    ctx = pipeline.process(ctx)

                    # Determine the response to return
                    if ctx.early_exit_response is not None:
                        response = ChatMessage(content=ctx.early_exit_response)
                    elif ctx.bot_response:
                        response = ctx.bot_response
                    else:
                        response = ChatMessage(content="", message_type=ChatMessageType.AI)

                    span.set_outputs({"response": response.content})

                    # Update instance state (for backward compat during migration)
                    self.experiment_session = ctx.experiment_session

                    return response
            except GenerationCancelled:
                return ChatMessage(content="", message_type=ChatMessageType.AI)

    def _create_context(self, message: BaseMessage) -> MessageProcessingContext:
        return MessageProcessingContext(
            message=message,
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            callbacks=self._get_callbacks(),
            sender=self._get_sender(),
            capabilities=self._get_capabilities(),
            trace_service=self.trace_service,
        )

    def _build_pipeline(self) -> MessageProcessingPipeline:
        """Build the default processing pipeline. Subclasses can override entirely.
        Core stages can be short-circuited by EarlyExitResponse.
        Terminal stages always run.
        """
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                SessionActivationStage(),
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                ConsentFlowStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(),
                SendingErrorHandlerStage(),
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )

    def _create_trace_service(self):
        return TracingService.create_for_experiment(self.experiment)

    def _get_capabilities(self) -> ChannelCapabilities:
        """Default capabilities from ClassVars. Override for runtime capabilities."""
        return ChannelCapabilities(
            supports_voice=self.voice_replies_supported,
            supports_files=getattr(self, "supports_multimedia", False),
            supports_conversational_consent=True,
            supported_message_types=self.supported_message_types,
            can_send_file=self._can_send_file,
        )

    def _can_send_file(self, file) -> bool:
        """Default: can't send files. Override in subclasses."""
        return False

    @abstractmethod
    def _get_callbacks(self) -> ChannelCallbacks:
        """Return channel-specific callbacks."""

    @abstractmethod
    def _get_sender(self) -> ChannelSender:
        """Return channel-specific sender."""

    def send_message_to_user(self, bot_message: str, files: list | None = None):
        """Send a bot-generated message to the user outside the normal pipeline.

        Used by ExperimentSession.try_send_message() for ad hoc bot messages
        (reminders, check-ins, event-triggered messages).

        Runs a mini pipeline: ResponseFormattingStage -> terminal stages.
        Voice/text decision, citation formatting, and file handling all apply.
        """
        files = files or []

        ctx = MessageProcessingContext(
            message=None,  # No inbound message for ad hoc
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            callbacks=self._get_callbacks(),
            sender=self._get_sender(),
            capabilities=self._get_capabilities(),
            trace_service=self.trace_service,
            participant_identifier=self.experiment_session.participant.identifier,
            bot_response=ChatMessage(content=bot_message),
            files_to_send=files,
        )

        mini_pipeline = MessageProcessingPipeline(
            core_stages=[
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(),
                SendingErrorHandlerStage(),
                PersistenceStage(),
                # No ActivityTrackingStage -- caller manages session activity
            ],
        )
        mini_pipeline.process(ctx)
