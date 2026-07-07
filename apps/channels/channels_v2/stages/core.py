from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import TYPE_CHECKING

from django.db.models import Q

from apps.annotations.models import TagCategories
from apps.channels.channels_v2.exceptions import EarlyAbort, EarlyExitResponse
from apps.channels.channels_v2.pipeline import MessageProcessingContext
from apps.channels.channels_v2.stages.base import ProcessingStage
from apps.channels.datamodels import Attachment
from apps.chat.bots import EvalsBot, EventBot, get_bot
from apps.chat.channels import (
    MARKDOWN_REF_PATTERN,
    MESSAGE_TYPES,
    strip_urls_and_emojis,
)
from apps.chat.const import STATUSES_FOR_COMPLETE_CHATS
from apps.chat.exceptions import AudioSynthesizeException, UserReportableError
from apps.chat.models import ChatAttachment, ChatMessage, ChatMessageMetadataKeys, ChatMessageType
from apps.events.models import StaticTriggerType
from apps.events.tasks import enqueue_static_triggers
from apps.experiments.models import (
    ExperimentSession,
    Participant,
    ParticipantData,
    SessionStatus,
    VoiceResponseBehaviours,
)
from apps.experiments.services import start_experiment_session
from apps.files.models import File, FilePurpose
from apps.ocs_notifications.notifications import (
    audio_synthesis_failure_notification,
    audio_transcription_failure_notification,
)
from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager
from apps.service_providers.tracing import TraceInfo
from apps.service_providers.tracing.base import SpanNotificationConfig

if TYPE_CHECKING:
    from apps.users.models import CustomUser

logger = logging.getLogger("ocs.channels")

RESET_COMMAND = "/reset"


def participant_identifier_filter(identifier: str, remote_id: str | None) -> Q:
    """Build the participant lookup filter for the BSUID rollout.

    Matches on the canonical identifier (the BSUID, post-rollout), plus the legacy
    identifier when one is available, so a returning user
    previously keyed by phone is linked to their messages now keyed by BSUID.
    """
    if remote_id and remote_id != identifier:
        return Q(identifier=identifier) | Q(identifier=remote_id)
    return Q(identifier=identifier)


def _associate_user(participant: Participant, participant_user: CustomUser | None) -> None:
    """Backfill the participant's user on first contact if it isn't set yet."""
    if participant_user and participant.user is None:
        participant.user = participant_user
        participant.save()


def get_or_create_participant(
    team,
    normalized_identifier: str,
    platform: str,
    participant_user: CustomUser | None,
    participant_id_filter: Q,
) -> Participant:
    """Look up or create a participant, handling disjunctive (BSUID OR legacy phone) filters.

    For a simple equality filter (the common case) this delegates straight to get_or_create.
    For a disjunction it probes first, so a match reuses the existing row (oldest wins) while
    a miss still creates the row keyed by the canonical normalized identifier -- never the phone.
    """
    if not normalized_identifier and not participant_user:
        raise ValueError("Either an identifier or a user must be specified!")
    if participant_user and normalized_identifier != participant_user.email:
        # This should technically never happen, since we disable the input for logged in users
        raise Exception(f"User {participant_user.email} cannot impersonate participant {normalized_identifier}")

    is_simple_filter = len(participant_id_filter.children) == 1
    if not is_simple_filter:
        existing = (
            Participant.objects.filter(participant_id_filter, team=team, platform=platform)
            .order_by("created_at", "id")
            .first()
        )
        if existing is not None:
            _associate_user(existing, participant_user)
            return existing

    participant, created = Participant.objects.get_or_create(
        team=team,
        identifier=normalized_identifier,
        platform=platform,
        defaults={"user": participant_user},
    )
    if not created:
        _associate_user(participant, participant_user)
    return participant


# ---------------------------------------------------------------------------
# ParticipantValidationStage
# ---------------------------------------------------------------------------


class ParticipantValidationStage(ProcessingStage):
    """Validates the participant is allowed to interact with this experiment."""

    span_input_fields = ("message.participant_id",)
    span_output_fields = ("participant_allowed",)

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.participant_identifier = ctx.message.participant_id

        if ctx.experiment.is_public:
            ctx.participant_allowed = True
            return

        ctx.participant_allowed = ctx.experiment.is_participant_allowed(ctx.participant_identifier)
        if not ctx.participant_allowed:
            raise EarlyExitResponse("Sorry, you are not allowed to chat to this bot")


# ---------------------------------------------------------------------------
# ParticipantResolverStage
# ---------------------------------------------------------------------------


class ParticipantResolverStage(ProcessingStage):
    """Resolves (or creates) the Participant record for the validated identifier.

    Always sets ctx.participant; new participants are created here so that
    SessionResolutionStage can use the FK directly without a separate creation step.

    If a participant_user is present in ctx.channel_context (e.g. web channels),
    it is associated with the participant on first contact or backfilled if missing.
    """

    span_input_fields = ("participant_identifier", "experiment_channel.platform")
    span_output_fields = ("participant.id", "participant_data.id")

    def process(self, ctx: MessageProcessingContext) -> None:
        normalized = ctx.experiment_channel.platform_enum.normalize_identifier(ctx.participant_identifier)
        participant_user = ctx.channel_context.get("participant_user")
        remote_id = ctx.message.remote_id if ctx.message else None
        ctx.participant = get_or_create_participant(
            team=ctx.experiment.team,
            normalized_identifier=normalized,
            platform=ctx.experiment_channel.platform,
            participant_user=participant_user,
            participant_id_filter=participant_identifier_filter(normalized, remote_id),
        )
        self._store_remote_id(ctx, remote_id)

        try:
            ctx.participant_data = ParticipantData.objects.for_experiment(ctx.experiment).get(
                participant=ctx.participant
            )
        except ParticipantData.DoesNotExist:
            ctx.participant_data = None

    def _store_remote_id(self, ctx: MessageProcessingContext, remote_id: str | None) -> None:
        """Persist the message's remote_id (e.g. the user's phone number) on the participant so it
        can be used as the send recipient -- the participant identifier may be a (non-sendable) BSUID."""
        participant = ctx.participant
        if not remote_id or participant is None:
            return
        if participant.remote_id == remote_id:
            return
        participant.remote_id = remote_id
        participant.save(update_fields=["remote_id"])


# ---------------------------------------------------------------------------
# SessionResolutionStage
# ---------------------------------------------------------------------------


class SessionResolutionStage(ProcessingStage):
    """Loads or creates an experiment session.

    Also handles the /reset command (Issue 7).
    For Web/Slack channels the session is pre-set on the context, so this
    stage becomes a no-op (Issue 4).
    """

    span_input_fields = ("participant.id",)
    span_output_fields = ("experiment_session.id", "experiment_session.status")

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return True

    def process(self, ctx: MessageProcessingContext) -> None:
        # Web/Slack channels pre-set the session -- nothing to do
        if ctx.experiment_session is not None:
            return

        # ParticipantResolverStage always creates/fetches the participant
        # before this stage runs, so ctx.participant is guaranteed to be set.
        assert ctx.participant is not None
        ctx.experiment_session = (
            ExperimentSession.objects.filter(
                experiment=ctx.experiment.get_working_version(),
                participant=ctx.participant,
            )
            .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
            .select_related("participant", "chat", "experiment_channel")
            .order_by("-created_at")
            .first()
        )

        # Check for /reset after loading the session so that _handle_reset
        # has access to ctx.experiment_session and can properly end it.
        if self._is_reset_request(ctx):
            self._handle_reset(ctx)
            return

        # Create a new session if none found
        if not ctx.experiment_session:
            ctx.experiment_session = self._create_session(ctx)

        # The trace was opened with session=None for channels that route
        # to a session lazily (e.g. EmailChannel). Back-fill it now.
        ctx.trace_service.set_session(ctx.experiment_session)

    def _is_reset_request(self, ctx: MessageProcessingContext) -> bool:
        return (
            ctx.message.content_type == MESSAGE_TYPES.TEXT and ctx.message.message_text.lower().strip() == RESET_COMMAND
        )

    def _handle_reset(self, ctx: MessageProcessingContext) -> None:
        if ctx.experiment_session:
            ctx.experiment_session.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_USER)

        ctx.experiment_session = self._create_session(ctx)
        ctx.trace_service.set_session(ctx.experiment_session)
        raise EarlyExitResponse("Conversation reset")

    def _create_session(self, ctx: MessageProcessingContext):
        """Delegates to the start_experiment_session service.

        Reuses the participant ParticipantResolverStage already resolved (e.g. a legacy
        phone-keyed row) so the session attaches to it rather than a new BSUID-keyed one.
        """
        participant = ctx.participant or Participant(
            identifier=ctx.participant_identifier,
            user=ctx.channel_context.get("participant_user"),
        )
        return start_experiment_session(
            working_experiment=ctx.experiment.get_working_version(),
            experiment_channel=ctx.experiment_channel,
            participant=participant,
            session_status=SessionStatus.SETUP,
        )


# ---------------------------------------------------------------------------
# MessageTypeValidationStage
# ---------------------------------------------------------------------------


class MessageTypeValidationStage(ProcessingStage):
    """Validates the message type is supported by this channel."""

    span_input_fields = ("message.content_type", "capabilities.supported_message_types")
    span_output_fields = ("human_message_tags",)

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

    span_output_fields = ("experiment_session.status",)

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        if ctx.experiment_session is None:
            return False
        return not ctx.experiment.conversational_consent_enabled or not ctx.experiment.consent_form_id

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.experiment_session.update_status(SessionStatus.ACTIVE)


# ---------------------------------------------------------------------------
# ConsentCheckStage
# ---------------------------------------------------------------------------


class ConsentCheckStage(ProcessingStage):
    """Platform consent gate (ParticipantData.system_metadata['consent']).

    Distinct from ConsentFlowStage: that one drives the conversational
    consent state machine (SETUP -> PENDING -> ACTIVE). This one enforces
    a platform-level consent flag managed outside the chat (e.g. CommCare
    Connect's auto-consent handshake, or Telegram revoking consent when
    the bot is blocked).

    When the gate blocks, the stage raises EarlyAbort to halt the pipeline
    silently -- no user-facing message is sent and no terminal stages run.
    Reporting an error would be wrong here: the participant has either
    withdrawn consent or the channel can no longer reach them.

    Configured via ChannelCapabilities.consent_config. When unset, the
    stage is skipped entirely.
    """

    span_input_fields = ("participant_data.id",)

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.capabilities.consent_config is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        config = ctx.capabilities.consent_config
        participant_data = ctx.participant_data  # cached_property; None if no row
        if participant_data is None:
            if config.strict:
                raise EarlyAbort()
            return

        if not participant_data.system_metadata.get("consent", config.default_consent):
            raise EarlyAbort()


# ---------------------------------------------------------------------------
# ConsentFlowStage
# ---------------------------------------------------------------------------


class ConsentFlowStage(ProcessingStage):
    """Handles the conversational consent state machine.

    This stage only manages consent state transitions. It does NOT:
      - Send messages (ResponseSendingStage handles that)
      - Persist to chat history (PersistenceStage handles that)
      - Invoke the bot (BotInteractionStage handles that)

    Sub-behaviors:
      - Builds the consent prompt text and raises EarlyExitResponse
      - Once consent is given, swaps the participant's original (pre-consent)
        message into ctx.user_query and lets the pipeline continue, so the
        normal bot-interaction path answers their first question. Falls back
        to the seed message when there was no substantive original message,
        and halts silently when there is neither.
    """

    USER_CONSENT_TEXT = "1"

    span_input_fields = ("experiment_session.status",)
    span_output_fields = ("experiment_session.status", "user_query")

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
            ]
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        session = ctx.experiment_session

        if session.status == SessionStatus.SETUP:
            session.update_status(SessionStatus.PENDING)
            raise EarlyExitResponse(self._build_consent_prompt(ctx))

        if session.status == SessionStatus.PENDING:
            if not self._user_gave_consent(ctx):
                raise EarlyExitResponse(self._build_consent_prompt(ctx))

            self._start_conversation(ctx)

    def _user_gave_consent(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None and ctx.user_query.strip() == self.USER_CONSENT_TEXT

    def _build_consent_prompt(self, ctx: MessageProcessingContext) -> str:
        """Build the consent prompt text. Does NOT send or persist -- just returns the string."""
        consent_text = ctx.experiment.consent_form.consent_text
        confirmation_text = ctx.experiment.consent_form.confirmation_text
        return f"{consent_text}\n\n{confirmation_text}"

    def _start_conversation(self, ctx: MessageProcessingContext) -> None:
        """Consent accepted: activate the session and hand off to the normal
        bot-interaction path by swapping the bot input into ctx.user_query.

        ctx.human_message stays as the consent-token message. The bot excludes
        the input message from the LLM history it builds, so the token never
        sits next to the swapped-in query in the LLM context, while remaining
        in the persisted history as the record of the consent reply.
        """
        ctx.experiment_session.update_status(SessionStatus.ACTIVE)

        # Original message wins over the seed message: answer what the
        # participant actually asked before they were interrupted for consent.
        original_message = self._get_original_message(ctx)
        if original_message is not None:
            ctx.user_query = original_message.content
            return

        if ctx.experiment.seed_message:
            ctx.user_query = ctx.experiment.seed_message
            return

        # Nothing to answer: no substantive original message and no seed
        # message. The session is now ACTIVE. Halt silently -- the consent
        # token must not be forwarded to the bot as the participant's first
        # prompt. (EarlyExitResponse("") would make terminal stages
        # persist/send an empty AI message; EarlyAbort skips them entirely.)
        raise EarlyAbort()

    def _get_original_message(self, ctx: MessageProcessingContext) -> ChatMessage | None:
        """Return the participant's first substantive message -- the one that
        triggered SETUP -> PENDING -- so it can be answered after consent.

        Returns None when the participant's first message was itself just the
        consent token (no prior content), so the caller falls back to the seed
        message / silent halt.
        """
        first_human_message = (
            ctx.experiment_session.chat.messages.filter(message_type=ChatMessageType.HUMAN)
            .order_by("created_at")
            .first()
        )
        if first_human_message is None:
            return None
        if first_human_message.content.strip() == self.USER_CONSENT_TEXT:
            return None
        return first_human_message


# ---------------------------------------------------------------------------
# QueryExtractionStage
# ---------------------------------------------------------------------------


class QueryExtractionStage(ProcessingStage):
    """Extracts the user's query from the message.

    For text messages, this is just message_text.
    For voice messages, this transcribes the audio.
    """

    span_input_fields = ("message.content_type",)
    span_output_fields = ("user_query",)

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

    span_input_fields = ("user_query",)
    span_output_fields = ("human_message.id",)

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        attachments_key = ChatMessageMetadataKeys.OCS_ATTACHMENT_FILE_IDS
        metadata = {attachments_key: []}
        is_voice = ctx.message.content_type == MESSAGE_TYPES.VOICE

        # Save voice note as attachment
        if is_voice and ctx.message.cached_media_data:
            # Guard against zero-byte / exhausted audio streams. Persisting a
            # File row without storage leads to ValueError later when the
            # attachment is downloaded (see OPEN-CHAT-STUDIO-248).
            ctx.message.cached_media_data.data.seek(0)
            audio_bytes = ctx.message.cached_media_data.data.read()
            if not audio_bytes:
                logger.warning(
                    "Skipping voice_note attachment for experiment=%s session=%s: empty audio stream",
                    ctx.experiment.id,
                    getattr(ctx.experiment_session, "id", None),
                )
            else:
                ctx.message.cached_media_data.data.seek(0)
                ext = ctx.message.cached_media_data.content_type.split("/")[1]
                file = File.create(
                    f"voice_note.{ext}",
                    ctx.message.cached_media_data.data,
                    ctx.experiment.team_id,
                    purpose=FilePurpose.MESSAGE_MEDIA,
                    content_type=ctx.message.cached_media_data.content_type,
                )
                ctx.experiment_session.chat.attach_files("voice_message", [file])
                metadata[attachments_key].append(file.id)

        # Record attachment IDs
        if ctx.message.attachments:
            metadata[attachments_key].extend([att.file_id for att in ctx.message.attachments])

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

    span_input_fields = ("user_query",)
    span_output_fields = ("bot_response.content", "files_to_send")

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def get_span_notification_config(self):
        return SpanNotificationConfig(permissions=["experiments.change_experiment"])

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.callbacks.on_submit_input_to_llm(ctx.participant_identifier)

        # Lazy bot creation -- reuse if already set on the context
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
    """Bot interaction for evaluations -- uses EvalsBot with in-memory participant_data.

    Reads participant_data from ctx.channel_context (set by EvaluationChannel),
    bypassing the DB-backed ParticipantData model.
    """

    span_input_fields = ("user_query",)
    span_output_fields = ("bot_response.content", "files_to_send")

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.bot = EvalsBot(
            ctx.experiment_session,
            ctx.experiment,
            ctx.trace_service,
            participant_data=ctx.channel_context["participant_data"],
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

    span_input_fields = ("bot_response.content",)
    span_output_fields = ("formatted_message", "voice_audio", "files_to_send", "unsupported_files")

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
            # Set formatted_message before stripping so the full text is available
            # as a fallback if voice delivery fails downstream
            ctx.formatted_message = message
            message, extracted_urls = strip_urls_and_emojis(message)
            urls_to_append = "\n".join(extracted_urls)
            urls_to_append = self._append_attachment_links(urls_to_append, unsupported_files, ctx)
            try:
                ctx.voice_audio = self._synthesize_voice(ctx, message)
                if urls_to_append:
                    ctx.additional_text_message = urls_to_append
            except AudioSynthesizeException:
                # Graceful fallback to text -- not an unrecoverable error
                logger.exception("Error generating voice response")
                audio_synthesis_failure_notification(ctx.experiment, session=ctx.experiment_session)
                ctx.voice_audio = None
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
        return f"{text}\n\n{'\n\n'.join(links)}\n"


# ---------------------------------------------------------------------------
# AttachmentHydrationStage
# ---------------------------------------------------------------------------


class AttachmentHydrationStage(ProcessingStage):
    """Hydrate Attachment objects from file IDs once a session exists.

    Channels that pre-persist inbound files in their webhook handler
    (e.g. EmailChannel) populate ctx.message.attachment_file_ids; this
    stage converts those IDs into Attachment objects with download_links
    that reference a real session. No-op for channels that don't use
    this pattern.
    """

    span_output_fields = ("message.attachments",)

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return bool(
            ctx.message
            and getattr(ctx.message, "attachment_file_ids", None)
            and not ctx.message.attachments
            and ctx.experiment_session is not None
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        files = self._get_files(ctx)
        if not files:
            return
        # Link the files to the session's Chat so the experiments:download_file
        # view's join (File → ChatAttachment → Chat → ExperimentSession) resolves
        # when an LLM provider fetches the download_link (or a user clicks it).
        chat_attachment, _ = ChatAttachment.objects.get_or_create(
            chat=ctx.experiment_session.chat,
            tool_type="ocs_attachments",
        )
        chat_attachment.files.add(*files)
        ctx.message.attachments = [
            Attachment.from_file(f, type="ocs_attachments", session_id=ctx.experiment_session.id) for f in files
        ]

    def _get_files(self, ctx: MessageProcessingContext) -> list[File]:
        """Return the Files to hydrate. Default impl resolves pre-persisted Files
        by ``ctx.message.attachment_file_ids``. Subclasses can override to acquire
        files differently (e.g. download from a remote channel)."""
        return list(
            File.objects.filter(
                id__in=ctx.message.attachment_file_ids,
                team_id=ctx.experiment.team_id,
            )
        )
