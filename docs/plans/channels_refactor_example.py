"""
Example implementation of the proposed channel refactoring architecture.

This file shows the base classes (context, stages, pipeline, callbacks, channel)
and two concrete channel implementations (Telegram, WhatsApp) to illustrate
how the pieces fit together.

This is NOT production code — it's a reference for understanding the proposed
architecture with all review decisions applied:
  - Issue 1:  EarlyExitResponse exception (stages raise, pipeline catches)
  - Issue 2:  Core stages vs terminal stages (pipeline orchestrates)
  - Issue 3:  Callbacks/sender/capabilities injected on the context (stages are zero-arg)
  - Issue 4:  Runtime `_get_capabilities()` for provider-dependent channels
  - Issue 5:  Pre-existing session passed on context (Web/Slack)
  - Issue 6:  Each stage handles its own errors
  - Issue 7:  Separate ChatMessageCreationStage
  - Issue 8:  /reset handled inside SessionResolutionStage
  - Issue 9:  ConsentFlowStage is explicit about sub-behaviors (no sending/history)
  - Issue 16: SessionActivationStage handles non-consent session activation (no side effects in should_run)
  - Update:   EarlyExitResponseStage (terminal) persists early_exit_response to chat history
  - Update:   ResponseSendingStage (terminal) is the sole message-sending stage
  - Update:   ActivityTrackingStage (terminal) always runs
  - Issue 13: select_related on session query
  - Issue 14: select_related on experiment FK lookups
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.chat.models import ChatMessage
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.speech_service import SynthesizedAudio
    from apps.service_providers.tracing import TracingService

logger = logging.getLogger("ocs.channels")


# ---------------------------------------------------------------------------
# 1. Channel Capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelCapabilities:
    """Describes what a channel can do. Populated at runtime — either from
    static ClassVars (Telegram) or from the messaging service (WhatsApp)."""

    supports_voice: bool = False
    supports_files: bool = False
    supports_conversational_consent: bool = True
    supported_message_types: list = field(default_factory=list)
    # File-level checking is delegated to a callable so that channel-specific
    # size/mime rules don't leak into the capabilities dataclass.
    can_send_file: callable = lambda file: False  # (File) -> bool


# ---------------------------------------------------------------------------
# 2. Channel Callbacks
# ---------------------------------------------------------------------------


class ChannelCallbacks:
    """Base class for channel-specific callback hooks.

    Default implementations are no-ops. Channels override the methods they care about.
    Methods that target a user receive `recipient: str` — not the full context.
    """

    def transcription_started(self, recipient: str) -> None:
        """Called when voice transcription starts (e.g. show 'uploading voice' indicator)."""

    def transcription_finished(self, recipient: str, transcript: str) -> None:
        """Called when voice transcription completes."""

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        """Send the transcript back to the user."""

    def submit_input_to_llm(self, recipient: str) -> None:
        """Called before LLM invocation (e.g. show 'typing' indicator)."""

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        """Retrieve audio content from the inbound message. Must be overridden
        by channels that support voice."""
        raise NotImplementedError("Channel must implement audio retrieval")


# ---------------------------------------------------------------------------
# 3. Channel Sender
# ---------------------------------------------------------------------------


class ChannelSender:
    """Abstracts how a channel delivers messages to the user.

    Default implementations raise NotImplementedError. Channels only override
    the methods their capabilities support — the capabilities layer gates which
    methods actually get called at runtime.
    """

    def send_text(self, text: str, recipient: str) -> None:
        raise NotImplementedError

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        raise NotImplementedError

    def send_file(self, file: File, recipient: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 4. EarlyExitResponse Exception
# ---------------------------------------------------------------------------


class EarlyExitResponse(Exception):
    """Raised by any core stage to short-circuit the pipeline.

    The pipeline orchestrator catches this, stores the message on
    ctx.early_exit_response, and then runs terminal stages.
    """

    def __init__(self, response: str):
        self.response = response
        super().__init__(response)


# ---------------------------------------------------------------------------
# 5. Message Processing Context
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
    bot: object | None = None  # Lazy — created in BotInteractionStage

    formatted_message: str | None = None
    voice_audio: SynthesizedAudio | None = None
    additional_text_message: str | None = None  # URLs/files sent after voice
    files_to_send: list = field(default_factory=list)
    unsupported_files: list = field(default_factory=list)

    # --- Control flow -------------------------------------------------------
    # Set by the pipeline orchestrator when it catches an EarlyExitResponse
    # exception. Stages do NOT set this directly; they raise EarlyExitResponse.
    early_exit_response: str | None = None

    # --- Observability ------------------------------------------------------
    processing_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 6. Processing Stage Base
# ---------------------------------------------------------------------------


class ProcessingStage(ABC):
    """Base class for stateless processing stages.

    Stages are zero-arg — all dependencies come via the context.
    Each stage is responsible for its own error handling.

    Stages do NOT check early_exit_response — the pipeline orchestrator
    handles short-circuiting. To exit early, raise EarlyExitResponse.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Override to add stage-specific preconditions.
        Default: always run. NOTE: This is NOT for early exit checking —
        the pipeline handles that."""
        return True

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process the context, modifying it in place.
        Raise EarlyExitResponse to short-circuit the pipeline."""

    def __call__(self, ctx: MessageProcessingContext) -> None:
        """Execute stage: check should_run, run inside a trace span."""
        if not self.should_run(ctx):
            return
        stage_name = self.__class__.__name__
        with ctx.trace_service.span(stage_name, inputs={}) as span:
            self.process(ctx)
            span.set_outputs({})


# ---------------------------------------------------------------------------
# 7. Core Stages (can be short-circuited by EarlyExitResponse)
# ---------------------------------------------------------------------------


class ParticipantValidationStage(ProcessingStage):
    """Validates the participant is allowed to interact with this experiment."""

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.participant_identifier = ctx.message.participant_id

        if ctx.experiment.is_public:
            ctx.participant_allowed = True
            return

        ctx.participant_allowed = ctx.experiment.is_participant_allowed(ctx.participant_identifier)
        if not ctx.participant_allowed:
            raise EarlyExitResponse("Sorry, you are not allowed to chat to this bot")


class SessionResolutionStage(ProcessingStage):
    """Loads or creates an experiment session.

    Also handles the /reset command (Issue 7).
    For Web/Slack channels the session is pre-set on the context, so this
    stage becomes a no-op (Issue 4).
    """

    RESET_COMMAND = "/reset"

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.participant_allowed

    def process(self, ctx: MessageProcessingContext) -> None:
        # Web/Slack channels pre-set the session — nothing to do
        if ctx.experiment_session is not None:
            return

        # Check for /reset before loading a session
        if self._is_reset_request(ctx):
            self._handle_reset(ctx)
            return

        # Try to load an existing active session (Issue 13: select_related)
        from apps.chat.const import STATUSES_FOR_COMPLETE_CHATS
        from apps.experiments.models import ExperimentSession

        ctx.experiment_session = (
            ExperimentSession.objects.filter(
                experiment=ctx.experiment.get_working_version(),
                participant__identifier=str(ctx.participant_identifier),
            )
            .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
            .select_related("participant", "chat", "experiment_channel")
            .order_by("-created_at")
            .first()
        )

        # Handle /reset on an existing session
        if ctx.experiment_session and self._is_reset_request(ctx):
            if ctx.experiment_session.user_already_engaged():
                self._handle_reset(ctx)
                return

        # Create a new session if none found
        if not ctx.experiment_session:
            ctx.experiment_session = self._create_session(ctx)

    def _is_reset_request(self, ctx: MessageProcessingContext) -> bool:
        from apps.chat.channels import MESSAGE_TYPES

        return (
            ctx.message.content_type == MESSAGE_TYPES.TEXT
            and ctx.message.message_text.lower().strip() == self.RESET_COMMAND
        )

    def _handle_reset(self, ctx: MessageProcessingContext) -> None:
        from apps.events.models import StaticTriggerType

        if ctx.experiment_session:
            ctx.experiment_session.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_USER)
        ctx.experiment_session = self._create_session(ctx)
        raise EarlyExitResponse("Conversation reset")

    def _create_session(self, ctx: MessageProcessingContext):
        """Delegates to the existing _start_experiment_session helper."""
        from apps.chat.channels import _start_experiment_session
        from apps.experiments.models import SessionStatus

        return _start_experiment_session(
            working_experiment=ctx.experiment.get_working_version(),
            experiment_channel=ctx.experiment_channel,
            participant_identifier=ctx.participant_identifier,
            session_status=SessionStatus.SETUP,
        )


class MessageTypeValidationStage(ProcessingStage):
    """Validates the message type is supported by this channel."""

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type not in ctx.capabilities.supported_message_types:
            # Use EventBot to generate a friendly response
            try:
                response = self._generate_unsupported_response(ctx)
            except Exception:
                response = f"Sorry, this channel only supports {ctx.capabilities.supported_message_types} messages."
                ctx.processing_errors.append("Failed to generate unsupported message response")
            raise EarlyExitResponse(response)

    def _generate_unsupported_response(self, ctx: MessageProcessingContext) -> str:
        """Uses EventBot to produce a natural-language error message."""
        from apps.chat.bots import EventBot
        from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager
        from apps.service_providers.tracing import TraceInfo

        history_manager = ExperimentHistoryManager(
            session=ctx.experiment_session, experiment=ctx.experiment, trace_service=ctx.trace_service
        )
        trace_info = TraceInfo(name="unsupported message", metadata={"message_type": ctx.message.content_type})
        supported = ctx.capabilities.supported_message_types
        prompt = f"Tell the user that they sent an unsupported message. You only support {supported} messages types."
        return EventBot(ctx.experiment_session, ctx.experiment, trace_info, history_manager).get_user_message(prompt)


class SessionActivationStage(ProcessingStage):
    """Activates the session when conversational consent is not required.

    When consent is disabled or no consent form is configured, this stage
    transitions the session directly to ACTIVE so downstream stages can
    proceed. This keeps the side effect out of ConsentFlowStage.should_run.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        if ctx.experiment_session is None:
            return False
        return not ctx.experiment.conversational_consent_enabled or not ctx.experiment.consent_form_id

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.experiments.models import SessionStatus

        ctx.experiment_session.update_status(SessionStatus.ACTIVE)


class ConsentFlowStage(ProcessingStage):
    """Handles the conversational consent state machine.

    This stage only manages consent state transitions and raises
    EarlyExitResponse. It does NOT:
      - Send messages (ResponseSendingStage handles that)
      - Persist to chat history (EarlyExitResponseStage handles that)

    Sub-behaviors:
      - Builds consent/survey prompt text and raises EarlyExitResponse
      - Handles seed message after consent is given
    """

    USER_CONSENT_TEXT = "1"

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        # Skip if channel doesn't support conversational consent
        if not ctx.capabilities.supports_conversational_consent:
            return False

        # Only run if consent is enabled and session is in a pre-conversation state
        from apps.experiments.models import SessionStatus

        return bool(
            ctx.experiment_session
            and ctx.experiment.conversational_consent_enabled
            and ctx.experiment.consent_form_id
            and ctx.experiment_session.status
            in [
                SessionStatus.SETUP,
                SessionStatus.PENDING,
                SessionStatus.PENDING_PRE_SURVEY,
            ]
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.experiments.models import SessionStatus

        session = ctx.experiment_session
        response = None

        if session.status == SessionStatus.SETUP:
            session.update_status(SessionStatus.PENDING)
            response = self._build_consent_prompt(ctx)

        elif session.status == SessionStatus.PENDING:
            if self._user_gave_consent(ctx):
                if not ctx.experiment.pre_survey:
                    response = self._start_conversation(ctx)
                else:
                    session.update_status(SessionStatus.PENDING_PRE_SURVEY)
                    response = self._build_survey_prompt(ctx)
            else:
                response = self._build_consent_prompt(ctx)

        elif session.status == SessionStatus.PENDING_PRE_SURVEY:
            if self._user_gave_consent(ctx):
                response = self._start_conversation(ctx)
            else:
                response = self._build_survey_prompt(ctx)

        if response is not None:
            raise EarlyExitResponse(response)

    def _user_gave_consent(self, ctx: MessageProcessingContext) -> bool:
        from apps.chat.channels import MESSAGE_TYPES

        return (
            ctx.message.content_type == MESSAGE_TYPES.TEXT
            and ctx.message.message_text.strip() == self.USER_CONSENT_TEXT
        )

    def _build_consent_prompt(self, ctx: MessageProcessingContext) -> str:
        """Build the consent prompt text. Does NOT send or persist — just returns the string."""
        consent_text = ctx.experiment.consent_form.consent_text
        confirmation_text = ctx.experiment.consent_form.confirmation_text
        return f"{consent_text}\n\n{confirmation_text}"

    def _build_survey_prompt(self, ctx: MessageProcessingContext) -> str:
        """Build the survey prompt text. Does NOT send or persist — just returns the string."""
        pre_survey_link = ctx.experiment_session.get_pre_survey_link(ctx.experiment)
        confirmation_text = ctx.experiment.pre_survey.confirmation_text
        return confirmation_text.format(survey_link=pre_survey_link)

    def _start_conversation(self, ctx: MessageProcessingContext) -> str | None:
        from apps.experiments.models import SessionStatus

        ctx.experiment_session.update_status(SessionStatus.ACTIVE)
        if ctx.experiment.seed_message:
            return self._process_seed_message(ctx)
        return None

    def _process_seed_message(self, ctx: MessageProcessingContext) -> str:
        """Invokes the bot with the seed message and returns the response text."""
        from apps.chat.bots import get_bot

        if not ctx.bot:
            ctx.bot = get_bot(ctx.experiment_session, ctx.experiment, ctx.trace_service)
        bot_response = ctx.bot.process_input(user_input=ctx.experiment.seed_message)
        return bot_response.content


class QueryExtractionStage(ProcessingStage):
    """Extracts the user's query from the message.

    For text messages, this is just message_text.
    For voice messages, this transcribes the audio.
    """

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.chat.channels import MESSAGE_TYPES

        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            try:
                ctx.user_query = self._transcribe_voice(ctx)
            except Exception as e:
                # Issue 5: stage handles its own error
                from apps.ocs_notifications.notifications import audio_transcription_failure_notification

                audio_transcription_failure_notification(ctx.experiment, platform=ctx.experiment_channel.platform)
                ctx.processing_errors.append(f"Voice transcription failed: {e}")
                raise
        else:
            ctx.user_query = ctx.message.message_text

    def _transcribe_voice(self, ctx: MessageProcessingContext) -> str:
        ctx.callbacks.transcription_started(ctx.participant_identifier)

        audio_file = ctx.callbacks.get_message_audio(ctx.message)
        transcript = self._do_transcription(ctx, audio_file)

        if ctx.experiment.echo_transcript:
            ctx.callbacks.echo_transcript(ctx.participant_identifier, transcript)

        ctx.callbacks.transcription_finished(ctx.participant_identifier, transcript)
        return transcript

    def _do_transcription(self, ctx: MessageProcessingContext, audio: BytesIO) -> str:
        from apps.chat.exceptions import UserReportableError

        if ctx.experiment.voice_provider:
            speech_service = ctx.experiment.voice_provider.get_speech_service()
            if speech_service.supports_transcription:
                return speech_service.transcribe_audio(audio)
        raise UserReportableError("Voice transcription is not available for this chatbot")


class ChatMessageCreationStage(ProcessingStage):
    """Creates the ChatMessage DB record for the user's message.

    This is a separate stage between query extraction and bot interaction,
    keeping extraction testable without the DB.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.annotations.models import TagCategories
        from apps.chat.channels import MESSAGE_TYPES
        from apps.chat.models import ChatMessage, ChatMessageType
        from apps.files.models import File, FilePurpose

        metadata = {"ocs_attachment_file_ids": []}
        is_voice = ctx.message.content_type == MESSAGE_TYPES.VOICE

        # Save voice note as attachment
        if is_voice and ctx.message.cached_media_data:
            ext = ctx.message.cached_media_data.content_type.split("/")[1]
            file = File.create(
                f"voice_note.{ext}",
                ctx.message.cached_media_data.data,
                ctx.experiment.team_id,
                purpose=FilePurpose.MESSAGE_MEDIA,
                content_type=ctx.message.cached_media_data.content_type,
            )
            ctx.experiment_session.chat.attach_files("voice_message", [file])
            metadata["ocs_attachment_file_ids"].append(file.id)

        # Record attachment IDs
        if ctx.message.attachments:
            metadata["ocs_attachment_file_ids"].extend([att.file_id for att in ctx.message.attachments])

        # Add trace metadata
        if ctx.trace_service:
            metadata.update(ctx.trace_service.get_trace_metadata())

        # Create the DB record
        ctx.human_message = ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.HUMAN,
            content=ctx.user_query,
            metadata=metadata,
        )

        # Tag voice messages
        if is_voice:
            ctx.human_message.create_and_add_tag("voice", ctx.experiment.team, TagCategories.MEDIA_TYPE)

        # Link to trace
        if ctx.trace_service:
            ctx.trace_service.set_input_message_id(ctx.human_message.id)


class BotInteractionStage(ProcessingStage):
    """Sends the user query to the bot and captures the response."""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.chat.bots import get_bot

        ctx.callbacks.submit_input_to_llm(ctx.participant_identifier)

        # Lazy bot creation — reuse if already created (e.g. by ConsentFlowStage seed message)
        if not ctx.bot:
            ctx.bot = get_bot(ctx.experiment_session, ctx.experiment, ctx.trace_service)

        try:
            ctx.bot_response = ctx.bot.process_input(
                ctx.user_query,
                attachments=ctx.message.attachments,
                human_message=ctx.human_message,
            )
            ctx.files_to_send = ctx.bot_response.get_attached_files() or []
        except Exception as e:
            # Stage handles its own error — inform the user via EarlyExitResponse
            error_msg = self._inform_user_of_error(ctx, e)
            ctx.processing_errors.append(f"Bot interaction failed: {e}")
            raise EarlyExitResponse(error_msg) from e

    def _inform_user_of_error(self, ctx: MessageProcessingContext, exception: Exception) -> str:
        from apps.chat.bots import EventBot
        from apps.service_providers.tracing import TraceInfo

        DEFAULT_ERROR = "Sorry, something went wrong while processing your message. Please try again later"
        trace_info = TraceInfo(name="error", metadata={"error": str(exception)})
        try:
            event_bot = EventBot(ctx.experiment_session, ctx.experiment, trace_info, trace_service=ctx.trace_service)
            msg = event_bot.get_user_message(
                "Tell the user that something went wrong while processing their message "
                "and that they should try again later."
            )
        except Exception:
            msg = DEFAULT_ERROR
        # Don't send directly — raise EarlyExitResponse and let
        # terminal stages handle delivery
        return msg


class ResponseFormattingStage(ProcessingStage):
    """Formats the bot response for the channel (text, voice, citations, files)."""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.bot_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.chat.channels import MESSAGE_TYPES, strip_urls_and_emojis
        from apps.experiments.models import VoiceResponseBehaviours

        message = ctx.bot_response.content
        files = ctx.files_to_send
        user_sent_voice = ctx.message.content_type == MESSAGE_TYPES.VOICE

        # Determine voice vs text reply
        should_reply_voice = False
        if ctx.capabilities.supports_voice and ctx.experiment.synthetic_voice:
            voice_config = ctx.experiment.voice_response_behaviour
            if (
                voice_config == VoiceResponseBehaviours.ALWAYS
                or voice_config == VoiceResponseBehaviours.RECIPROCAL
                and user_sent_voice
            ):
                should_reply_voice = True

        # Split files by channel support
        supported_files = []
        unsupported_files = []
        if ctx.capabilities.supports_files:
            for f in files:
                if ctx.capabilities.can_send_file(f):
                    supported_files.append(f)
                else:
                    unsupported_files.append(f)
        else:
            unsupported_files = list(files)

        if should_reply_voice:
            message, extracted_urls = strip_urls_and_emojis(message)
            ctx.formatted_message = message
            ctx.voice_audio = self._synthesize_voice(ctx, message)
            # Collect URLs and file links for a follow-up text message
            extra_parts = list(extracted_urls)
            for f in unsupported_files:
                extra_parts.append(f"{f.name}\n{f.download_link(ctx.experiment_session.id)}")
            if extra_parts:
                ctx.additional_text_message = "\n".join(extra_parts)
        else:
            message, uncited_files = self._format_reference_section(message, files, ctx)
            unsupported_uncited = [f for f in unsupported_files if f in uncited_files]
            message = self._append_attachment_links(message, unsupported_uncited, ctx)
            ctx.formatted_message = message

        ctx.files_to_send = supported_files
        ctx.unsupported_files = unsupported_files

    def _synthesize_voice(self, ctx: MessageProcessingContext, text: str):
        voice_provider = ctx.experiment.voice_provider
        synthetic_voice = ctx.experiment.synthetic_voice
        if ctx.bot:
            bot_voice = ctx.bot.get_synthetic_voice()
            if bot_voice:
                synthetic_voice = bot_voice
        speech_service = voice_provider.get_speech_service()
        return speech_service.synthesize_voice(text, synthetic_voice)

    def _format_reference_section(self, text: str, files: list, ctx: MessageProcessingContext):
        """Processes markdown-style file references. Same logic as current
        ChannelBase._format_reference_section, but uses ctx.capabilities.can_send_file."""
        from apps.chat.channels import MARKDOWN_REF_PATTERN

        text = re.sub(r"\[\^([^\]]+)\]", r"[\1]", text)
        cited_files = set()
        if not files:
            return text, []

        files_by_citation_text = {file.citation_text: file for file in files}

        def format_match(match):
            ref_id = match.group("ref")
            citation_text = match.group("citation_text")
            citation_url = match.group("citation_url")
            file = files_by_citation_text.get(citation_text)
            if not file:
                return match.group(0)
            cited_files.add(file)
            if ctx.capabilities.can_send_file(file):
                return f"[{ref_id}]: {file.citation_text}"
            return f"[{ref_id}]: {file.citation_text} ({citation_url})"

        text = re.compile(MARKDOWN_REF_PATTERN, re.MULTILINE).sub(format_match, text)
        uncited = [f for f in files if f not in cited_files]
        return text, uncited

    def _append_attachment_links(self, text: str, files: list, ctx: MessageProcessingContext) -> str:
        if not files:
            return text
        links = [f"{f.name}\n{f.download_link(ctx.experiment_session.id)}" for f in files]
        return f"{text}\n\n{''.join(links)}"


# ---------------------------------------------------------------------------
# 8. Terminal Stages (always run — never short-circuited)
# ---------------------------------------------------------------------------


class EarlyExitResponseStage(ProcessingStage):
    """TERMINAL STAGE: Persists early_exit_response to chat history.

    When a core stage raises EarlyExitResponse, the pipeline catches it
    and sets ctx.early_exit_response. This stage then creates the
    corresponding ChatMessage DB record so the response appears in
    the conversation history.

    Uses _add_to_history (moved out of ConsentFlowStage) to persist.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.early_exit_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.experiment_session:
            self._add_to_history(ctx, ctx.early_exit_response)

    def _add_to_history(self, ctx: MessageProcessingContext, content: str) -> None:
        """Persist the early exit response as an AI message in chat history."""
        from apps.chat.models import ChatMessage, ChatMessageType

        ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.AI,
            content=content,
        )


class ResponseSendingStage(ProcessingStage):
    """TERMINAL STAGE: Sends the response to the user.

    This is the ONLY stage that sends messages to the user.
    It always fires — handling both early exit responses and normal
    bot responses. All other stages that need to communicate with
    the user raise EarlyExitResponse and let this stage handle delivery.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        # Always fire when there's something to send
        return ctx.formatted_message is not None or ctx.early_exit_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.ocs_notifications.notifications import (
            audio_synthesis_failure_notification,
            file_delivery_failure_notification,
        )

        if ctx.early_exit_response:
            # Early exit path — send the early exit response text
            ctx.sender.send_text(ctx.early_exit_response, ctx.participant_identifier)
            return

        # Normal path — send formatted bot response
        if ctx.voice_audio:
            try:
                ctx.sender.send_voice(ctx.voice_audio, ctx.participant_identifier)
            except Exception:
                logger.exception("Error sending voice response")
                audio_synthesis_failure_notification(ctx.experiment, session=ctx.experiment_session)
                # Fallback to text
                ctx.sender.send_text(ctx.formatted_message, ctx.participant_identifier)

            if ctx.additional_text_message:
                ctx.sender.send_text(ctx.additional_text_message, ctx.participant_identifier)
        else:
            ctx.sender.send_text(ctx.formatted_message, ctx.participant_identifier)

        # Send supported file attachments
        for file in ctx.files_to_send:
            try:
                ctx.sender.send_file(file, ctx.participant_identifier)
            except Exception as e:
                logger.exception(e)
                platform_title = ctx.experiment_channel.platform_enum.title()
                file_delivery_failure_notification(
                    ctx.experiment,
                    platform_title=platform_title,
                    content_type=file.content_type,
                    session=ctx.experiment_session,
                )
                download_link = file.download_link(ctx.experiment_session.id)
                ctx.sender.send_text(download_link, ctx.participant_identifier)


class ActivityTrackingStage(ProcessingStage):
    """TERMINAL STAGE: Updates session activity timestamp and experiment version tracking."""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.experiment_session is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        from django.utils import timezone

        assert ctx.experiment_session is not None
        session = ctx.experiment_session
        update_fields = ["last_activity_at"]
        session.last_activity_at = timezone.now()

        if ctx.experiment.is_a_version:
            version_number = ctx.experiment.version_number
            current_versions = session.experiment_versions or []
            if version_number not in current_versions:
                session.experiment_versions = current_versions + [version_number]
                update_fields.append("experiment_versions")

        session.save(update_fields=update_fields)


# ---------------------------------------------------------------------------
# 9. Pipeline Orchestrator
# ---------------------------------------------------------------------------


class MessageProcessingPipeline:
    """Orchestrates message processing through core and terminal stages.

    Core stages run sequentially and can be short-circuited by raising
    EarlyExitResponse. Terminal stages always run — they handle both
    normal responses and early exit responses.

    The pipeline is the ONLY place that handles EarlyExitResponse.
    Individual stages never check ctx.early_exit_response.
    """

    def __init__(
        self,
        core_stages: list[ProcessingStage],
        terminal_stages: list[ProcessingStage],
    ):
        self.core_stages = [s for s in core_stages if s is not None]
        self.terminal_stages = [s for s in terminal_stages if s is not None]

    def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
        """Run core stages, catch EarlyExitResponse, then run terminal stages."""
        try:
            for stage in self.core_stages:
                stage(ctx)
        except EarlyExitResponse as e:
            ctx.early_exit_response = e.response

        # Terminal stages always run — regardless of early exit
        for stage in self.terminal_stages:
            stage(ctx)

        return ctx


# ---------------------------------------------------------------------------
# 10. Channel Base
# ---------------------------------------------------------------------------


class ChannelBase(ABC):
    """Base channel — builds the pipeline and provides the entry point.

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
        """Main entry point — runs a message through the processing pipeline."""
        from apps.chat.models import ChatMessage, ChatMessageType
        from apps.service_providers.llm_service.runnables import GenerationCancelled
        from apps.teams.utils import current_team

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
                MessageTypeValidationStage(),
                SessionActivationStage(),
                ConsentFlowStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                EarlyExitResponseStage(),
                ResponseSendingStage(),
                ActivityTrackingStage(),
            ],
        )

    def _create_trace_service(self):
        from apps.service_providers.tracing import TracingService

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


# ---------------------------------------------------------------------------
# 11. Concrete Channel: Telegram
# ---------------------------------------------------------------------------


class TelegramCallbacks(ChannelCallbacks):
    """Telegram-specific callbacks: typing indicators, audio download, transcript echo."""

    def __init__(self, sender: TelegramSender, telegram_bot):
        self._sender = sender
        self._bot = telegram_bot

    def transcription_started(self, recipient: str) -> None:
        self._bot.send_chat_action(chat_id=recipient, action="upload_voice")

    def submit_input_to_llm(self, recipient: str) -> None:
        self._bot.send_chat_action(chat_id=recipient, action="typing")

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        self._sender.send_text(text=f"I heard: {transcript}", recipient=recipient)

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        import httpx

        from apps.channels import audio as audio_utils

        file_url = self._bot.get_file_url(message.media_id)
        response = httpx.get(file_url)
        response.raise_for_status()
        ogg_audio = BytesIO(response.content)
        return audio_utils.convert_audio(ogg_audio, target_format="wav", source_format="ogg")


class TelegramSender(ChannelSender):
    """Sends messages via the Telegram Bot API."""

    def __init__(self, telegram_bot):
        self._bot = telegram_bot

    def send_text(self, text: str, recipient: str) -> None:
        from telebot.apihelper import ApiTelegramException
        from telebot.util import antiflood, smart_split

        try:
            for chunk in smart_split(text):
                antiflood(self._bot.send_message, recipient, text=chunk)
        except ApiTelegramException as e:
            self._handle_api_error(e, recipient)

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        from telebot.apihelper import ApiTelegramException
        from telebot.util import antiflood

        try:
            antiflood(
                self._bot.send_voice,
                recipient,
                voice=audio.audio,
                duration=audio.duration,
            )
        except ApiTelegramException as e:
            self._handle_api_error(e, recipient)

    def send_file(self, file: File, recipient: str) -> None:
        from telebot.util import antiflood

        mime = file.content_type
        main_type = mime.split("/")[0]
        match main_type:
            case "image":
                method, arg = self._bot.send_photo, "photo"
            case "video":
                method, arg = self._bot.send_video, "video"
            case "audio":
                method, arg = self._bot.send_audio, "audio"
            case _:
                method, arg = self._bot.send_document, "document"
        antiflood(method, recipient, **{arg: file.file})

    def _handle_api_error(self, e, recipient: str):
        from apps.chat.exceptions import ChannelException

        if e.error_code == 403 and "bot was blocked by the user" in e.description:
            # Could update participant consent here
            raise ChannelException("Bot was blocked by the user") from e
        raise ChannelException(f"Telegram API error: {e.description}") from e


class TelegramChannel(ChannelBase):
    """Telegram channel implementation."""

    voice_replies_supported = True
    supports_multimedia = True

    def __init__(self, experiment, experiment_channel, experiment_session=None):
        from telebot import TeleBot

        # Telegram-specific setup before super().__init__
        self.telegram_bot = TeleBot(experiment_channel.extra_data["bot_token"], threaded=False)

        super().__init__(experiment, experiment_channel, experiment_session)

    def _get_sender(self) -> ChannelSender:
        return TelegramSender(self.telegram_bot)

    def _get_callbacks(self) -> ChannelCallbacks:
        return TelegramCallbacks(sender=self._get_sender(), telegram_bot=self.telegram_bot)

    def _get_capabilities(self) -> ChannelCapabilities:
        from apps.chat.channels import MESSAGE_TYPES

        return ChannelCapabilities(
            supports_voice=True,
            supports_files=True,
            supports_conversational_consent=True,
            supported_message_types=[MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE],
            can_send_file=self._can_send_file,
        )

    def _can_send_file(self, file) -> bool:
        mime = file.content_type
        size = file.content_size or 0
        if mime.startswith("image/"):
            return size <= 10 * 1024 * 1024
        elif mime.startswith(("video/", "audio/", "application/")):
            return size <= 50 * 1024 * 1024
        return False


# ---------------------------------------------------------------------------
# 12. Concrete Channel: WhatsApp
# ---------------------------------------------------------------------------


class WhatsappSender(ChannelSender):
    """Sends messages via the WhatsApp messaging service (Twilio or TurnIO)."""

    def __init__(self, messaging_service, from_number: str):
        self._service = messaging_service
        self._from = from_number

    def send_text(self, text: str, recipient: str) -> None:
        from apps.channels.models import ChannelPlatform

        self._service.send_text_message(message=text, from_=self._from, to=recipient, platform=ChannelPlatform.WHATSAPP)

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        from apps.channels.models import ChannelPlatform

        self._service.send_voice_message(audio, from_=self._from, to=recipient, platform=ChannelPlatform.WHATSAPP)

    def send_file(self, file: File, recipient: str) -> None:
        from apps.channels.models import ChannelPlatform

        self._service.send_file_to_user(
            from_=self._from,
            to=recipient,
            platform=ChannelPlatform.WHATSAPP,
            file=file,
            download_link=file.download_link(experiment_session_id=None),
            # Note: session_id for download_link would come from ctx in real impl
        )


class WhatsappCallbacks(ChannelCallbacks):
    """WhatsApp callbacks — echo transcript via text message."""

    def __init__(self, sender: WhatsappSender):
        self._sender = sender

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        self._sender.send_text(text=f"I heard: {transcript}", recipient=recipient)


class WhatsappChannel(ChannelBase):
    """WhatsApp channel — capabilities are determined at runtime from the
    messaging service (Twilio vs TurnIO). This is Issue 3 in action."""

    def __init__(self, experiment, experiment_channel, experiment_session=None):
        super().__init__(experiment, experiment_channel, experiment_session)
        # Lazily resolved
        self._messaging_service = None

    @property
    def messaging_service(self):
        if self._messaging_service is None:
            self._messaging_service = self.experiment_channel.messaging_provider.get_messaging_service()
        return self._messaging_service

    def _get_callbacks(self) -> ChannelCallbacks:
        return WhatsappCallbacks(sender=self._get_sender())

    def _get_sender(self) -> ChannelSender:
        from_number = self.experiment_channel.extra_data["number"]
        return WhatsappSender(self.messaging_service, from_number)

    def _get_capabilities(self) -> ChannelCapabilities:
        """Issue 3: Runtime capabilities from the messaging service."""
        return ChannelCapabilities(
            supports_voice=self.messaging_service.voice_replies_supported,
            supports_files=self.messaging_service.supports_multimedia,
            supports_conversational_consent=True,
            supported_message_types=self.messaging_service.supported_message_types,
            can_send_file=self.messaging_service.can_send_file,
        )


# ---------------------------------------------------------------------------
# 13. How it all fits together
# ---------------------------------------------------------------------------


def example_message_flow():
    """Pseudocode showing a complete message flow."""

    # 1. Webhook receives a Telegram message, creates channel instance
    #    (same as today)
    experiment = ...
    experiment_channel = ...
    telegram_message = ...
    channel = TelegramChannel(experiment, experiment_channel)

    # 2. Call new_user_message (same public API as today)
    response = channel.new_user_message(telegram_message)

    # 3. Internally:
    #    a. ChannelBase._create_context(message) builds the context with:
    #       - message, experiment, channel
    #       - TelegramCallbacks, TelegramSender, ChannelCapabilities
    #       - trace_service
    #
    #    b. ChannelBase._build_pipeline() builds:
    #       core_stages: [ParticipantValidation, SessionResolution,
    #        MessageTypeValidation, SessionActivation, ConsentFlow,
    #        QueryExtraction, ChatMessageCreation, BotInteraction,
    #        ResponseFormatting]
    #       terminal_stages: [EarlyExitResponse, ResponseSending, ActivityTracking]
    #
    #    c. pipeline.process(ctx) runs core stages in sequence:
    #       - ParticipantValidation: sets ctx.participant_identifier, ctx.participant_allowed
    #       - SessionResolution: loads/creates ctx.experiment_session (with select_related)
    #       - MessageTypeValidation: checks message type against capabilities
    #       - SessionActivation: activates session when consent is not required
    #       - ConsentFlow: handles consent state machine if needed
    #       - QueryExtraction: sets ctx.user_query (transcribes voice if needed)
    #       - ChatMessageCreation: creates ChatMessage DB record
    #       - BotInteraction: calls bot.process_input, sets ctx.bot_response
    #       - ResponseFormatting: formats message, handles voice/text/files
    #
    #    d. If any core stage raises EarlyExitResponse, remaining core stages
    #       are skipped. The pipeline catches the exception and stores the
    #       response on ctx.early_exit_response.
    #
    #    e. Terminal stages always run:
    #       - EarlyExitResponseStage: persists early exit response to chat history
    #       - ResponseSending: delivers to user via TelegramSender
    #       - ActivityTracking: updates session timestamps

    return response
