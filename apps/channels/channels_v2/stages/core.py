from __future__ import annotations

import logging
import re
from io import BytesIO

from apps.annotations.models import TagCategories
from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.channels.channels_v2.pipeline import MessageProcessingContext
from apps.channels.channels_v2.stages.base import ProcessingStage
from apps.chat.bots import EvalsBot, EventBot, get_bot
from apps.chat.channels import MARKDOWN_REF_PATTERN, MESSAGE_TYPES, _start_experiment_session, strip_urls_and_emojis
from apps.chat.const import STATUSES_FOR_COMPLETE_CHATS
from apps.chat.exceptions import AudioSynthesizeException, UserReportableError
from apps.chat.models import ChatMessage, ChatMessageType
from apps.events.models import StaticTriggerType
from apps.events.tasks import enqueue_static_triggers
from apps.experiments.models import ExperimentSession, SessionStatus, VoiceResponseBehaviours
from apps.files.models import File, FilePurpose
from apps.ocs_notifications.notifications import (
    audio_synthesis_failure_notification,
    audio_transcription_failure_notification,
)
from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager
from apps.service_providers.tracing import TraceInfo
from apps.service_providers.tracing.base import SpanNotificationConfig

logger = logging.getLogger("ocs.channels")

RESET_COMMAND = "/reset"


# ---------------------------------------------------------------------------
# ParticipantValidationStage
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


# ---------------------------------------------------------------------------
# SessionResolutionStage
# ---------------------------------------------------------------------------


class SessionResolutionStage(ProcessingStage):
    """Loads or creates an experiment session.

    Also handles the /reset command (Issue 7).
    For Web/Slack channels the session is pre-set on the context, so this
    stage becomes a no-op (Issue 4).
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.participant_allowed

    def process(self, ctx: MessageProcessingContext) -> None:
        # Web/Slack channels pre-set the session -- nothing to do
        if ctx.experiment_session is not None:
            return

        # Check for /reset before loading a session
        if self._is_reset_request(ctx):
            self._handle_reset(ctx)
            return

        # Try to load an existing active session (Issue 13: select_related)
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

        # Create a new session if none found
        if not ctx.experiment_session:
            ctx.experiment_session = self._create_session(ctx)

    def _is_reset_request(self, ctx: MessageProcessingContext) -> bool:
        return (
            ctx.message.content_type == MESSAGE_TYPES.TEXT and ctx.message.message_text.lower().strip() == RESET_COMMAND
        )

    def _handle_reset(self, ctx: MessageProcessingContext) -> None:
        if ctx.experiment_session:
            ctx.experiment_session.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_USER)
        else:
            # Load and end the existing session if one exists (common path for
            # channels that don't pre-set sessions, e.g. API/Telegram)
            existing = (
                ExperimentSession.objects.filter(
                    experiment=ctx.experiment.get_working_version(),
                    participant__identifier=str(ctx.participant_identifier),
                )
                .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
                .order_by("-created_at")
                .first()
            )
            if existing:
                existing.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_USER)

        ctx.experiment_session = self._create_session(ctx)
        raise EarlyExitResponse("Conversation reset")

    def _create_session(self, ctx: MessageProcessingContext):
        """Delegates to the existing _start_experiment_session helper."""
        return _start_experiment_session(
            working_experiment=ctx.experiment.get_working_version(),
            experiment_channel=ctx.experiment_channel,
            participant_identifier=ctx.participant_identifier,
            participant_user=ctx.channel_context.get("participant_user"),
            session_status=SessionStatus.SETUP,
        )


# ---------------------------------------------------------------------------
# MessageTypeValidationStage
# ---------------------------------------------------------------------------


class MessageTypeValidationStage(ProcessingStage):
    """Validates the message type is supported by this channel."""

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type not in ctx.capabilities.supported_message_types:
            # Tag the human message for analytics (PersistenceStage applies the tag)
            ctx.human_message_tags.append(("unsupported_message_type", TagCategories.ERROR))

            # Use EventBot to generate a friendly response
            try:
                response = self._generate_unsupported_response(ctx)
            except Exception:
                response = f"Sorry, this channel only supports {ctx.capabilities.supported_message_types} messages."
                ctx.processing_errors.append("Failed to generate unsupported message response")
            raise EarlyExitResponse(response)

    def _generate_unsupported_response(self, ctx: MessageProcessingContext) -> str:
        """Uses EventBot to produce a natural-language error message."""
        history_manager = ExperimentHistoryManager(
            session=ctx.experiment_session, experiment=ctx.experiment, trace_service=ctx.trace_service
        )
        trace_info = TraceInfo(name="unsupported message", metadata={"message_type": ctx.message.content_type})
        supported = ctx.capabilities.supported_message_types
        prompt = f"Tell the user that they sent an unsupported message. You only support {supported} messages types."
        return EventBot(ctx.experiment_session, ctx.experiment, trace_info, history_manager).get_user_message(prompt)


# ---------------------------------------------------------------------------
# SessionActivationStage
# ---------------------------------------------------------------------------


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
        ctx.experiment_session.update_status(SessionStatus.ACTIVE)


# ---------------------------------------------------------------------------
# ConsentFlowStage
# ---------------------------------------------------------------------------


class ConsentFlowStage(ProcessingStage):
    """Handles the conversational consent state machine.

    This stage only manages consent state transitions and raises
    EarlyExitResponse. It does NOT:
      - Send messages (ResponseSendingStage handles that)
      - Persist to chat history (PersistenceStage handles that)

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
        return ctx.user_query is not None and ctx.user_query.strip() == self.USER_CONSENT_TEXT

    def _build_consent_prompt(self, ctx: MessageProcessingContext) -> str:
        """Build the consent prompt text. Does NOT send or persist -- just returns the string."""
        consent_text = ctx.experiment.consent_form.consent_text
        confirmation_text = ctx.experiment.consent_form.confirmation_text
        return f"{consent_text}\n\n{confirmation_text}"

    def _build_survey_prompt(self, ctx: MessageProcessingContext) -> str:
        """Build the survey prompt text. Does NOT send or persist -- just returns the string."""
        pre_survey_link = ctx.experiment_session.get_pre_survey_link(ctx.experiment)
        confirmation_text = ctx.experiment.pre_survey.confirmation_text
        return confirmation_text.format(survey_link=pre_survey_link)

    def _start_conversation(self, ctx: MessageProcessingContext) -> str | None:
        ctx.experiment_session.update_status(SessionStatus.ACTIVE)
        if ctx.experiment.seed_message:
            return self._process_seed_message(ctx)
        return None

    def _process_seed_message(self, ctx: MessageProcessingContext) -> str:
        """Invokes the bot with the seed message and returns the response text.

        Note: bot.process_input() persists the AI response internally.
        PersistenceStage detects this (ctx.bot_response is not None) and
        skips creating a duplicate AI ChatMessage for the early exit response.
        """
        if not ctx.bot:
            ctx.bot = get_bot(ctx.experiment_session, ctx.experiment, ctx.trace_service)
        ctx.bot_response = ctx.bot.process_input(user_input=ctx.experiment.seed_message)
        return ctx.bot_response.content


# ---------------------------------------------------------------------------
# QueryExtractionStage
# ---------------------------------------------------------------------------


class QueryExtractionStage(ProcessingStage):
    """Extracts the user's query from the message.

    For text messages, this is just message_text.
    For voice messages, this transcribes the audio.
    """

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            try:
                ctx.user_query = self._transcribe_voice(ctx)
            except Exception as e:
                # Stage handles its own error
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
        if ctx.experiment.voice_provider:
            speech_service = ctx.experiment.voice_provider.get_speech_service()
            if speech_service.supports_transcription:
                return speech_service.transcribe_audio(audio)
        raise UserReportableError("Voice transcription is not available for this chatbot")


# ---------------------------------------------------------------------------
# ChatMessageCreationStage
# ---------------------------------------------------------------------------


class ChatMessageCreationStage(ProcessingStage):
    """Creates the ChatMessage DB record for the user's message.

    This is a separate stage between query extraction and bot interaction,
    keeping extraction testable without the DB.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
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

        # Fire NEW_HUMAN_MESSAGE trigger (gated by capability)
        if ctx.capabilities.supports_static_triggers:
            enqueue_static_triggers.delay(ctx.experiment_session.id, StaticTriggerType.NEW_HUMAN_MESSAGE)


# ---------------------------------------------------------------------------
# BotInteractionStage
# ---------------------------------------------------------------------------


class BotInteractionStage(ProcessingStage):
    """Sends the user query to the bot and captures the response.

    Exceptions are NOT caught here -- the pipeline's catch-all error handler
    generates the user-facing error message, sets ctx.early_exit_response,
    runs terminal stages, and then re-raises.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def get_span_notification_config(self):
        return SpanNotificationConfig(permissions=["experiments.change_experiment"])

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.callbacks.submit_input_to_llm(ctx.participant_identifier)

        # Lazy bot creation -- reuse if already created (e.g. by ConsentFlowStage seed message)
        if not ctx.bot:
            ctx.bot = get_bot(ctx.experiment_session, ctx.experiment, ctx.trace_service)

        ctx.bot_response = ctx.bot.process_input(
            ctx.user_query,
            attachments=ctx.message.attachments,
            human_message=ctx.human_message,
        )
        ctx.files_to_send = ctx.bot_response.get_attached_files() or []


# ---------------------------------------------------------------------------
# EvalsBotInteractionStage
# ---------------------------------------------------------------------------


class EvalsBotInteractionStage(ProcessingStage):
    """Specialized bot interaction for evaluations.

    Uses EvalsBot instead of get_bot(). Reads participant_data from
    ctx.channel_context (a dict set by EvaluationChannel, not the
    DB-backed ParticipantData model).
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
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


# ---------------------------------------------------------------------------
# ResponseFormattingStage
# ---------------------------------------------------------------------------


class ResponseFormattingStage(ProcessingStage):
    """Formats the bot response for the channel (text, voice, citations, files).

    Voice synthesis failures are caught here and gracefully degraded to
    text -- the user still gets a useful response. This is NOT an
    unrecoverable error, so it does not propagate to the pipeline's catch-all.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.bot_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        message = ctx.bot_response.content
        files = ctx.files_to_send
        user_sent_voice = ctx.message is not None and ctx.message.content_type == MESSAGE_TYPES.VOICE

        # Determine voice vs text reply
        should_reply_voice = False
        if ctx.capabilities.supports_voice_replies and ctx.experiment.synthetic_voice:
            voice_config = ctx.experiment.voice_response_behaviour
            if voice_config == VoiceResponseBehaviours.ALWAYS or (
                voice_config == VoiceResponseBehaviours.RECIPROCAL and user_sent_voice
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
            urls_to_append = "\n".join(extracted_urls)
            urls_to_append = self._append_attachment_links(urls_to_append, unsupported_files, ctx)
            try:
                ctx.voice_audio = self._synthesize_voice(ctx, message)
                ctx.formatted_message = message
                if urls_to_append:
                    ctx.additional_text_message = urls_to_append
            except AudioSynthesizeException:
                # Graceful fallback to text -- not an unrecoverable error
                logger.exception("Error generating voice response")
                audio_synthesis_failure_notification(ctx.experiment, session=ctx.experiment_session)
                ctx.voice_audio = None
                ctx.formatted_message = f"{message}\n\n{urls_to_append}"
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
