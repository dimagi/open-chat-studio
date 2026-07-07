from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from apps.channels.capabilities import ChannelCapabilities
from apps.channels.pipeline import MessageProcessingContext, MessageProcessingPipeline
from apps.channels.stages.core import (
    AttachmentHydrationStage,
    BotInteractionStage,
    ChatMessageCreationStage,
    ConsentCheckStage,
    ConsentFlowStage,
    MessageTypeValidationStage,
    ParticipantResolverStage,
    ParticipantValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
    SessionActivationStage,
    SessionResolutionStage,
)
from apps.channels.stages.terminal import (
    ActivityTrackingStage,
    DeliveryErrorHandler,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.chat.const import STATUSES_FOR_COMPLETE_CHATS
from apps.chat.models import ChatMessage, ChatMessageType
from apps.events.models import StaticTriggerType
from apps.experiments.models import ExperimentSession, Participant, SessionStatus
from apps.experiments.services import start_experiment_session
from apps.service_providers.llm_service.runnables import GenerationCancelled
from apps.service_providers.tracing import TracingService
from apps.teams.utils import current_team

if TYPE_CHECKING:
    from apps.channels.callbacks import ChannelCallbacks
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.channels.sender import ChannelSender
    from apps.experiments.models import Experiment

logger = logging.getLogger("ocs.channels")


class ChannelBase(ABC):
    """Base channel -- builds the pipeline and provides the entry point.

    Channels are now "pipeline builders + callback/sender providers."
    The heavy processing logic lives in the stages.
    """

    # Class-level defaults (overridden by subclasses)
    voice_replies_supported: ClassVar[bool] = False
    supports_multimedia: ClassVar[bool] = False
    supported_message_types: ClassVar[tuple[str, ...]] = ()
    attachment_hydration_stage_class: ClassVar[type[AttachmentHydrationStage]] = AttachmentHydrationStage

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
                    inputs={"input": message.model_dump(mode="json")},
                ) as span:
                    ctx = pipeline.process(ctx)

                    # Determine the response to return
                    if ctx.early_exit_response is not None:
                        response = ChatMessage(content=ctx.early_exit_response, message_type=ChatMessageType.AI)
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
        ctx = MessageProcessingContext(
            message=message,
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            callbacks=self._get_callbacks(),
            sender=self._get_sender(),
            capabilities=self._get_capabilities(),
            trace_service=self.trace_service,
        )
        return ctx

    def _build_pipeline(self) -> MessageProcessingPipeline:
        """Build the default processing pipeline. Subclasses can override entirely.
        Core stages can be short-circuited by EarlyExitResponse.
        Terminal stages always run.
        """
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                ParticipantResolverStage(),
                ConsentCheckStage(),
                SessionResolutionStage(),
                SessionActivationStage(),
                self.attachment_hydration_stage_class(),
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                ConsentFlowStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(should_voice_fallback_to_text=self._should_voice_fallback_to_text),
                SendingErrorHandlerStage(error_handlers=self._get_delivery_error_handlers()),
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )

    def _get_delivery_error_handlers(self) -> list[DeliveryErrorHandler]:
        """Return channel-specific handlers that get first crack at send failures.

        Each handler returns True to claim the exception (chain stops); otherwise
        the next handler runs, falling through to generic notification behavior.
        """
        return []

    def _should_voice_fallback_to_text(self, exc: Exception) -> bool:
        """Whether a failed voice delivery should be retried as a text message.

        Called by ResponseSendingStage with the exception raised from send_voice.
        Override per channel for platform failures where text can still get through.
        """
        return False

    def _create_trace_service(self):
        return TracingService.create_for_experiment(self.experiment)

    def _get_capabilities(self) -> ChannelCapabilities:
        """Default capabilities from ClassVars. Override for runtime capabilities."""
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=self.supports_multimedia,
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

    @classmethod
    def start_new_session(
        cls,
        working_experiment: Experiment,
        experiment_channel: ExperimentChannel,
        participant_identifier: str,
        participant_user=None,
        session_status: SessionStatus = SessionStatus.ACTIVE,
        timezone: str | None = None,
        session_external_id: str | None = None,
        metadata: dict | None = None,
    ) -> ExperimentSession:
        return start_experiment_session(
            working_experiment,
            experiment_channel,
            Participant(identifier=participant_identifier, user=participant_user),
            session_status,
            timezone,
            session_external_id,
            metadata,
        )

    def ensure_session_exists_for_participant(self, identifier: str, new_session: bool = False) -> None:
        """Ensure an experiment session exists for the given participant identifier.

        Used when the bot initiates a conversation (e.g. ``trigger_bot_message_task``) and
        no inbound message is available to drive the regular pipeline. The resolved session
        is assigned to ``self.experiment_session``.

        If ``new_session`` is True, any existing non-completed session for this participant
        is ended and a fresh one is created.
        """
        assert self.experiment_session is None or self.experiment_session.participant.identifier == identifier, (
            "Participant identifier does not match the existing session"
        )

        working_experiment = self.experiment.get_working_version()
        existing_session = (
            ExperimentSession.objects.filter(
                experiment=working_experiment,
                participant__identifier=str(identifier),
                experiment_channel__platform=self.experiment_channel.platform,
            )
            .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
            .order_by("-created_at")
            .first()
        )

        if new_session and existing_session:
            existing_session.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_USER)
            existing_session = None

        if existing_session is None:
            existing_session = start_experiment_session(
                working_experiment=working_experiment,
                experiment_channel=self.experiment_channel,
                participant=Participant(identifier=identifier),
                session_status=SessionStatus.SETUP,
            )

        self.experiment_session = existing_session

    def send_message_to_user(self, bot_message: str, files: list | None = None):
        """Send a bot-generated message to the user outside the normal pipeline.

        Used by ExperimentSession.try_send_message() for ad hoc bot messages
        (reminders, check-ins, event-triggered messages).

        Runs a mini pipeline: ResponseFormattingStage -> terminal stages.
        Voice/text decision, citation formatting, and file handling all apply.
        """
        if self.experiment_session is None:
            raise ValueError("Cannot send ad hoc message without an experiment session")

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
            bot_response=ChatMessage(content=bot_message, message_type=ChatMessageType.AI),
            files_to_send=files,
        )

        mini_pipeline = MessageProcessingPipeline(
            core_stages=[
                ParticipantResolverStage(),
                ConsentCheckStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(should_voice_fallback_to_text=self._should_voice_fallback_to_text),
                SendingErrorHandlerStage(error_handlers=self._get_delivery_error_handlers()),
                PersistenceStage(),
                # No ActivityTrackingStage -- caller manages session activity
            ],
        )
        mini_pipeline.process(ctx)
