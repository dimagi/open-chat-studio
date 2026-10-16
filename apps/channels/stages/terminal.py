from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from django.utils import timezone

from apps.annotations.models import TagCategories
from apps.channels.const import MESSAGE_TYPES
from apps.channels.pipeline import MessageProcessingContext, generate_user_message
from apps.channels.stages.base import ProcessingStage
from apps.channels.stages.core import RESET_COMMAND
from apps.chat.models import ChatMessage, ChatMessageMetadataKeys, ChatMessageType, ModerationStatus
from apps.files.models import File, FilePurpose
from apps.moderation.const import FLAGGED_MESSAGE
from apps.moderation.services import (
    add_strike,
    get_moderation_result,
    record_moderation_result,
    remove_turn_from_pipeline_histories,
    strike_categories,
)
from apps.ocs_notifications.notifications import (
    file_delivery_failure_notification,
    message_delivery_failure_notification,
    moderation_skipped_notification,
)

if TYPE_CHECKING:
    from apps.service_providers.moderation_service.schemas import ModerationOutput
    from apps.service_providers.speech_service import SynthesizedAudio

DeliveryErrorHandler = Callable[[MessageProcessingContext, Exception], bool]
"""Channel-specific delivery error handler.

Returns True if the handler claimed the exception (chain stops); False to let
the next handler — or the generic notification fallback — run.
"""

logger = logging.getLogger("ocs.channels")


class MessageDeliveryFailure(Exception):
    def __init__(
        self,
        original_exc: Exception,
        *,
        context: str,
    ) -> None:
        super().__init__(str(original_exc))
        self.original_exc = original_exc
        self.context = context


class FileDeliveryFailure(Exception):
    def __init__(
        self,
        original_exc: Exception,
        *,
        file,
    ) -> None:
        super().__init__(str(original_exc))
        self.original_exc = original_exc
        self.file = file


# ---------------------------------------------------------------------------
# ModerationResultStage
# ---------------------------------------------------------------------------


class ModerationResultStage(ProcessingStage):
    """TERMINAL STAGE: Records the moderation result on the human message and replaces the reply to a flagged one.

    A flagged message also gives the participant a strike (see `add_strike`), and the flagged turn is removed
    from the pipeline's node and named histories, so it is not replayed.

    A terminal stage so that it also runs when a core stage exits early. A failure to get a result is
    recorded as a skip, and the response is sent as normal. Any other error is logged, and the later
    stages still send and store the response.
    """

    FLAGGED_PROMPT = (
        "Tell the user that their last message was flagged by content moderation, so it will not be answered."
    )

    span_input_fields = ("moderation_task_id",)
    span_output_fields = ("human_message.moderation_status", "human_message.moderation_detail.skip_reason")

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.moderation_task_id is not None and ctx.human_message is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        try:
            self._apply_result(ctx)
        except Exception:
            logger.exception("Could not record the moderation result for task %s", ctx.moderation_task_id)

    def _apply_result(self, ctx: MessageProcessingContext) -> None:
        assert ctx.moderation_task_id is not None
        assert ctx.human_message is not None
        output, skip_reason = get_moderation_result(ctx.moderation_task_id)
        status = record_moderation_result(message=ctx.human_message, output=output, skip_reason=skip_reason)
        if skip_reason is not None:
            moderation_skipped_notification(ctx.experiment, session=ctx.experiment_session, reason=skip_reason)
        elif status == ModerationStatus.FLAGGED:
            assert output is not None
            self._add_strike(ctx, output)
            remove_turn_from_pipeline_histories(human_message=ctx.human_message, bot_response=ctx.bot_response)
            if ctx.bot_response is not None:
                self._replace_response(ctx)

    def _add_strike(self, ctx: MessageProcessingContext, output: ModerationOutput) -> None:
        """Give the participant a strike, logging any error so the reply is still replaced."""
        assert ctx.experiment_session is not None
        assert ctx.human_message is not None
        try:
            add_strike(
                participant=ctx.experiment_session.participant,
                experiment=ctx.experiment,
                chat_message=ctx.human_message,
                categories=strike_categories(output),
            )
        except Exception:
            logger.exception("Could not add a strike for message %s", ctx.human_message.id)

    def _replace_response(self, ctx: MessageProcessingContext) -> None:
        """Replace the stored bot reply with a notice that the message will not be answered, and send only that.

        The fixed notice is saved before EventBot writes one, so the original reply is not in the
        conversation history EventBot reads.
        """
        self._save_reply(ctx, FLAGGED_MESSAGE)
        reply = generate_user_message(
            ctx=ctx, prompt=self.FLAGGED_PROMPT, trace_name="moderation flagged", fallback=FLAGGED_MESSAGE
        )
        if reply != FLAGGED_MESSAGE:
            self._save_reply(ctx, reply)

    def _save_reply(self, ctx: MessageProcessingContext, reply: str) -> None:
        """Save `reply` as the bot response, without attachments, and make it the reply to send."""
        bot_response = ctx.bot_response
        assert bot_response is not None
        previous_content, previous_metadata = bot_response.content, bot_response.metadata
        bot_response.content = reply
        bot_response.metadata = {
            key: value
            for key, value in previous_metadata.items()
            if key not in ChatMessageMetadataKeys.attachment_keys()
        }
        try:
            bot_response.save(update_fields=["content", "metadata", "updated_at"])
        except Exception:
            # Keep the in-memory message, which web and API channels return, matching the database.
            bot_response.content, bot_response.metadata = previous_content, previous_metadata
            raise
        ctx.replace_response(reply)


# ---------------------------------------------------------------------------
# ResponseSendingStage
# ---------------------------------------------------------------------------


class ResponseSendingStage(ProcessingStage):
    """TERMINAL STAGE: Sends the response to the user.

    This is the ONLY stage that sends messages to the user.
    Handles both early exit responses and normal bot responses.

    All sending is wrapped in a single outer try/except. On failure,
    the exception is appended to ctx.sending_exceptions.

    - Text/voice: _send_text/_send_voice raise MessageDeliveryFailure.
    - Voice failures the channel's should_voice_fallback_to_text predicate
      approves are retried as text (ctx.formatted_message) instead of raising.
    - Files: each failure is wrapped in FileDeliveryFailure and appended
      to ctx.sending_exceptions. A download link fallback is sent via
      _send_text; if that also fails, the MessageDeliveryFailure propagates
      to the outer catch.

    SendingErrorHandlerStage processes ctx.sending_exceptions for
    notifications and platform-specific side effects.
    """

    def __init__(self, should_voice_fallback_to_text: Callable[[Exception], bool] | None = None) -> None:
        self._should_voice_fallback_to_text = should_voice_fallback_to_text

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.formatted_message is not None or ctx.early_exit_response is not None

    def get_span_inputs(self, ctx: MessageProcessingContext) -> dict:
        """Record what is being delivered so a send failure has context on the span."""
        message = ctx.early_exit_response if ctx.early_exit_response is not None else ctx.formatted_message
        return {
            "recipient": ctx.participant_identifier,
            "message": message,
            "is_voice": ctx.voice_audio is not None,
            "files": [file.name for file in ctx.files_to_send],
        }

    def process(self, ctx: MessageProcessingContext) -> None:
        try:
            if ctx.early_exit_response:
                self._send_text(ctx, ctx.early_exit_response, ctx.participant_identifier)
                self._flush(ctx)
                return

            # Normal path -- send formatted bot response
            if ctx.voice_audio:
                voice_sent = self._send_voice(ctx, ctx.voice_audio, ctx.participant_identifier)
                if voice_sent and ctx.additional_text_message:
                    self._send_text(ctx, ctx.additional_text_message, ctx.participant_identifier)
            else:
                self._send_text(ctx, ctx.formatted_message, ctx.participant_identifier)

            for file in ctx.files_to_send:
                self._send_file(ctx, file, ctx.participant_identifier)

            self._flush(ctx)
        except Exception as e:
            ctx.sending_exceptions.append(e)
            ctx.processing_errors.append(f"Send failed: {e}")

    def _send_text(self, ctx: MessageProcessingContext, text: str, recipient: str) -> None:
        if not text:
            return
        try:
            ctx.sender.send_text(text, recipient)
        except Exception as e:
            raise MessageDeliveryFailure(
                e,
                context="text message",
            ) from e

    def _send_voice(self, ctx: MessageProcessingContext, audio: SynthesizedAudio, recipient: str) -> bool:
        """Returns True if voice was delivered, False if the reply fell back to text."""
        try:
            ctx.sender.send_voice(audio, recipient)
            return True
        except Exception as e:
            if self._voice_fallback_to_text(ctx, e, recipient):
                return False
            raise MessageDeliveryFailure(
                e,
                context="voice message",
            ) from e

    def _voice_fallback_to_text(self, ctx: MessageProcessingContext, exc: Exception, recipient: str) -> bool:
        """Send ctx.formatted_message as text when the channel approves the voice failure."""
        if self._should_voice_fallback_to_text is None or not ctx.formatted_message:
            return False
        if not self._should_voice_fallback_to_text(exc):
            return False
        logger.info("Voice delivery failed (%s); falling back to text", exc)
        # Response is no longer voice: PersistenceStage must not tag it or attach audio
        ctx.voice_audio = None
        self._send_text(ctx, ctx.formatted_message, recipient)
        return True

    def _flush(self, ctx: MessageProcessingContext) -> None:
        try:
            ctx.sender.flush()
        except Exception as e:
            raise MessageDeliveryFailure(
                e,
                context="message",
            ) from e

    def _send_file(self, ctx: MessageProcessingContext, file, recipient: str) -> None:
        try:
            ctx.sender.send_file(file, recipient, ctx.experiment_session.id)
        except Exception as e:
            ctx.sending_exceptions.append(
                FileDeliveryFailure(
                    e,
                    file=file,
                )
            )
            download_link = file.download_link(ctx.experiment_session.id)
            self._send_text(ctx, download_link, recipient)


# ---------------------------------------------------------------------------
# SendingErrorHandlerStage
# ---------------------------------------------------------------------------


class SendingErrorHandlerStage(ProcessingStage):
    """TERMINAL STAGE: Handles notifications and side effects from send failures.

    Processes ctx.sending_exceptions. Channels can plug in platform-specific
    behavior by passing a chain of ``DeliveryErrorHandler`` callables; each
    handler returns True to claim the exception. Unclaimed exceptions fall
    through to generic notification handling.
    """

    def __init__(self, error_handlers: Sequence[DeliveryErrorHandler] = ()) -> None:
        self._error_handlers: tuple[DeliveryErrorHandler, ...] = tuple(error_handlers)

    def get_span_inputs(self, ctx: MessageProcessingContext) -> dict:
        return {"sending_exceptions": [str(exc) for exc in ctx.sending_exceptions]}

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return bool(ctx.sending_exceptions)

    def process(self, ctx: MessageProcessingContext) -> None:
        for exc in ctx.sending_exceptions:
            self._handle_exception(ctx, exc)

    def _handle_exception(self, ctx: MessageProcessingContext, exc: Exception) -> None:
        """Handle a single sending exception."""
        for handler in self._error_handlers:
            if handler(ctx, exc):
                return

        if isinstance(exc, MessageDeliveryFailure):
            logger.exception("Message delivery failure: %s", exc, exc_info=exc.original_exc)
            message_delivery_failure_notification(
                ctx.experiment,
                session=ctx.experiment_session,
                platform_title=ctx.experiment_channel.platform_enum.title(),
                context=exc.context,
                recipient=ctx.participant_identifier,
            )
            return

        if isinstance(exc, FileDeliveryFailure):
            logger.exception("File delivery failure: %s", exc, exc_info=exc.original_exc)
            file_delivery_failure_notification(
                ctx.experiment,
                platform_title=ctx.experiment_channel.platform_enum.title(),
                content_type=exc.file.content_type,
                session=ctx.experiment_session,
            )
            return

        raise exc  # Unknown exception -- propagate to fail the task


# ---------------------------------------------------------------------------
# PersistenceStage
# ---------------------------------------------------------------------------


class PersistenceStage(ProcessingStage):
    """TERMINAL STAGE: Persists chat messages and voice attachments.

    Runs after ResponseSendingStage and SendingErrorHandlerStage.
    Persists regardless of whether sending succeeded -- chat history
    serves as an audit trail.

    Handles three persistence concerns:
    1. Human message tags: Applies any tags set by earlier stages
       (e.g. "unsupported_message_type" from MessageTypeValidationStage).
    2. Early exit responses: Creates an AI ChatMessage DB record for the
       early exit response text.  Detects the /reset command from the
       user's inbound message and skips ALL persistence (matching current
       behavior where reset is intentionally not recorded).
    3. Voice attachments: Tags the bot response as "voice" and saves
       the synthesized audio as a file attachment.
    """

    span_input_fields = ("early_exit_response", "human_message_tags", "voice_audio")

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        if not ctx.experiment_session:
            return False

        # Skip all persistence for /reset -- matching current behavior
        # where the reset command is intentionally not recorded.
        if self._is_reset_command(ctx):
            return False

        return ctx.early_exit_response is not None or ctx.voice_audio is not None or bool(ctx.human_message_tags)

    def _is_reset_command(self, ctx: MessageProcessingContext) -> bool:
        """Check if the user's inbound message was the /reset command."""
        return (
            ctx.message is not None
            and ctx.message.content_type == MESSAGE_TYPES.TEXT
            and ctx.message.message_text.lower().strip() == RESET_COMMAND
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        self._apply_human_message_tags(ctx)
        self._persist_early_exit_response(ctx)
        self._attach_voice_audio(ctx)

    def _apply_human_message_tags(self, ctx: MessageProcessingContext) -> None:
        """Apply the tags earlier stages set on the inbound message."""
        if not (ctx.human_message and ctx.human_message_tags):
            return

        for tag_name, tag_category in ctx.human_message_tags:
            ctx.human_message.create_and_add_tag(tag_name, ctx.experiment.team, tag_category)

    def _persist_early_exit_response(self, ctx: MessageProcessingContext) -> None:
        """Record the early exit response as an AI message.

        Skipped when ctx.bot_response exists -- bot.process_input() already
        persisted the AI message (e.g. when the catch-all error handler
        set early_exit_response after BotInteractionStage succeeded).
        """
        if ctx.early_exit_response is None or ctx.bot_response is not None:
            return

        # Both halves of the link, as the bot path does when it saves a response:
        # the FK so the trace points at the message, and the metadata the message
        # renders its own trace icon from. The bot path never ran, so nothing else
        # sets either.
        trace_metadata = ctx.trace_service.get_trace_metadata() if ctx.trace_service else {}
        ai_message = ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.AI,
            content=ctx.early_exit_response,
            metadata={**trace_metadata, **ctx.early_exit_metadata},
        )
        if ctx.trace_service:
            ctx.trace_service.set_output_message_id(ai_message.id)

    def _attach_voice_audio(self, ctx: MessageProcessingContext) -> None:
        """Tag the bot response as voice and attach the synthesized audio."""
        if ctx.voice_audio is None or ctx.bot_response is None:
            return

        ctx.bot_response.create_and_add_tag("voice", ctx.experiment.team, TagCategories.MEDIA_TYPE)
        ctx.voice_audio.audio.seek(0)
        # Guard against zero-byte / exhausted audio streams. Persisting a
        # File row without storage leads to ValueError later when the
        # attachment is downloaded (see OPEN-CHAT-STUDIO-248).
        if not ctx.voice_audio.audio.read():
            logger.warning(
                "Skipping voice_note.ogg attachment for experiment=%s session=%s: empty audio stream",
                ctx.experiment.id,
                getattr(ctx.experiment_session, "id", None),
            )
            return

        ctx.voice_audio.audio.seek(0)
        file = File.create(
            "voice_note.ogg",
            ctx.voice_audio.audio,
            ctx.experiment.team_id,
            purpose=FilePurpose.MESSAGE_MEDIA,
            content_type=ctx.voice_audio.content_type,
        )
        ctx.bot_response.add_attachment_id(file.id)


# ---------------------------------------------------------------------------
# ActivityTrackingStage
# ---------------------------------------------------------------------------


class ActivityTrackingStage(ProcessingStage):
    """TERMINAL STAGE: Updates session activity timestamp and experiment version tracking."""

    span_output_fields = ("experiment_session.last_activity_at", "experiment_session.experiment_versions")

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.experiment_session is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        assert ctx.experiment_session is not None
        session = ctx.experiment_session
        update_fields = ["last_activity_at"]
        session.last_activity_at = timezone.now()

        if ctx.experiment.is_a_version:
            version_number = ctx.experiment.version_number
            current_versions = session.experiment_versions or []
            if version_number not in current_versions:
                session.experiment_versions = [*current_versions, version_number]  # ty: ignore[invalid-assignment]
                update_fields.append("experiment_versions")

        session.save(update_fields=update_fields)
