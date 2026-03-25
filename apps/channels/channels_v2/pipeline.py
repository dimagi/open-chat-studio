from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.chat.bots import EventBot
from apps.chat.exceptions import ChatException
from apps.service_providers.llm_service.runnables import GenerationCancelled
from apps.service_providers.tracing import TraceInfo

if TYPE_CHECKING:
    from apps.channels.channels_v2.callbacks import ChannelCallbacks
    from apps.channels.channels_v2.capabilities import ChannelCapabilities
    from apps.channels.channels_v2.sender import ChannelSender
    from apps.channels.channels_v2.stages.base import ProcessingStage
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.chat.bots import PipelineBot
    from apps.chat.models import ChatMessage
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.speech_service import SynthesizedAudio
    from apps.service_providers.tracing import TracingService

logger = logging.getLogger("ocs.channels")


# ---------------------------------------------------------------------------
# Message Processing Context
# ---------------------------------------------------------------------------


@dataclass
class MessageProcessingContext:
    """Carries all state through the processing pipeline.

    Created once at the start of `new_user_message`, then passed to every stage.
    Stages read from and write to this object.
    """

    # --- Input (set at creation, immutable during processing) ---------------
    message: BaseMessage
    experiment: Experiment
    experiment_channel: ExperimentChannel

    # --- Services (injected at creation) ------------------------------------
    callbacks: ChannelCallbacks
    sender: ChannelSender
    capabilities: ChannelCapabilities
    trace_service: TracingService

    # --- State (populated during processing) --------------------------------
    experiment_session: ExperimentSession | None = None
    participant_identifier: str | None = None
    participant_allowed: bool = False

    user_query: str | None = None
    transcript: str | None = None

    human_message: ChatMessage | None = None  # DB record of the user's message
    bot_response: ChatMessage | None = None
    bot: PipelineBot | None = None  # Lazy -- created in BotInteractionStage

    formatted_message: str | None = None
    voice_audio: SynthesizedAudio | None = None
    additional_text_message: str | None = None  # URLs/files sent after voice
    files_to_send: list[File] = field(default_factory=list)
    unsupported_files: list[File] = field(default_factory=list)

    # --- Control flow -------------------------------------------------------
    # Set by the pipeline orchestrator when it catches an EarlyExitResponse
    # exception OR when the catch-all error handler generates an error message.
    # Stages do NOT set this directly; they raise EarlyExitResponse.
    early_exit_response: str | None = None

    # --- Sending error ------------------------------------------------------
    # Set by ResponseSendingStage when a send fails. Read by
    # SendingErrorHandlerStage for platform-specific side effects.
    sending_exception: Exception | None = None

    # --- Observability ------------------------------------------------------
    processing_errors: list[str] = field(default_factory=list)

    # --- Human message tags -------------------------------------------------
    # Set by stages (e.g. MessageTypeValidationStage) to tag the human
    # message during persistence.  PersistenceStage reads this list and
    # applies the tags to the human ChatMessage record.
    human_message_tags: list[tuple[str, str]] = field(default_factory=list)
    # Each entry is (tag_name, tag_category) -- e.g. ("unsupported_message_type", TagCategories.ERROR)

    # --- Channel-specific context -------------------------------------------
    # WORKAROUND for EvaluationChannel only. EvaluationChannel uses this to
    # pass participant_data (a dict) to EvalsBotInteractionStage, bypassing
    # the normal DB-backed ParticipantData lookup. This should NOT be used
    # as a general-purpose extension mechanism. If other channels need
    # channel-specific data, revisit this design.
    channel_context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------


class MessageProcessingPipeline:
    """Orchestrates message processing through core and terminal stages.

    Core stages run sequentially and can be short-circuited by raising
    EarlyExitResponse. Terminal stages always run -- they handle both
    normal responses and early exit responses.

    The pipeline is the ONLY place that handles EarlyExitResponse and
    unexpected exceptions. Individual stages never check
    ctx.early_exit_response.

    Error handling has two tiers:
    1. EarlyExitResponse -- intentional short-circuit by a stage
    2. Unexpected Exception -- catch-all generates an error message
       via EventBot (preserving ChatException distinction), falls back
       to DEFAULT_ERROR_RESPONSE_TEXT, runs terminal stages, then
       RE-RAISES so the caller knows processing failed.
    """

    DEFAULT_ERROR_RESPONSE_TEXT = "Sorry, something went wrong while processing your message. Please try again later"

    def __init__(
        self,
        core_stages: list[ProcessingStage],
        terminal_stages: list[ProcessingStage],
        passthrough_exceptions: tuple[type[Exception], ...] | None = None,
    ):
        if passthrough_exceptions is None:
            passthrough_exceptions = (GenerationCancelled,)
        self.core_stages = [s for s in core_stages if s is not None]
        self.terminal_stages = [s for s in terminal_stages if s is not None]
        self.passthrough_exceptions = passthrough_exceptions

    def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
        """Run core stages, catch exceptions, then run terminal stages.

        1. Run core stages sequentially. If any raises EarlyExitResponse,
           remaining core stages are skipped.
        2. If any raises a passthrough exception, re-raise immediately
           without error handling or terminal stages.
        3. If any raises an unexpected exception, generate an error message
           and set ctx.early_exit_response.
        4. Run terminal stages unconditionally (they always fire).
        5. If there was an unexpected exception, re-raise it after terminal
           stages complete.
        """
        unexpected_exception = None

        try:
            for stage in self.core_stages:
                stage(ctx)
        except EarlyExitResponse as e:
            ctx.early_exit_response = e.response
        except self.passthrough_exceptions:
            # Passthrough exceptions (e.g. GenerationCancelled) propagate
            # immediately -- no error message generation, no terminal stages.
            raise
        except Exception as e:
            unexpected_exception = e
            ctx.early_exit_response = self._generate_error_message(ctx, e)
            ctx.processing_errors.append(str(e))

        # Terminal stages always run -- regardless of early exit or error
        for stage in self.terminal_stages:
            stage(ctx)

        # Re-raise unexpected exceptions after terminal stages complete
        if unexpected_exception is not None:
            raise unexpected_exception

        return ctx

    def _generate_error_message(self, ctx: MessageProcessingContext, exception: Exception) -> str:
        """Generate a user-facing error message using EventBot.

        Preserves the ChatException distinction: ChatException instances
        get a more specific prompt that includes the error message.
        Falls back to DEFAULT_ERROR_RESPONSE_TEXT if EventBot fails.

        Maps to the old _inform_user_of_error() but WITHOUT sending --
        sending is ResponseSendingStage's responsibility.
        """
        trace_info = TraceInfo(name="error", metadata={"error": str(exception)})
        prompt = (
            "Tell the user that something went wrong while processing their message"
            " and that they should try again later."
        )
        if isinstance(exception, ChatException):
            prompt = (
                f"Tell the user that you were unable to process their message and that "
                f"they should try again later or adjust the message type or contents "
                f"according to the following error message: {exception}"
            )

        event_bot = EventBot(ctx.experiment_session, ctx.experiment, trace_info, trace_service=ctx.trace_service)
        try:
            return event_bot.get_user_message(prompt)
        except Exception:
            logger.exception("Failed to generate error message via EventBot, falling back to default")
            return self.DEFAULT_ERROR_RESPONSE_TEXT
