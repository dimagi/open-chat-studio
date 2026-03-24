from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.utils import timezone
from telebot.apihelper import ApiTelegramException

from apps.annotations.models import TagCategories
from apps.channels.channels_v2.pipeline import MessageProcessingContext
from apps.channels.channels_v2.stages.base import ProcessingStage
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ParticipantData
from apps.files.models import File, FilePurpose
from apps.ocs_notifications.notifications import (
    file_delivery_failure_notification,
    message_delivery_failure_notification,
)

if TYPE_CHECKING:
    from apps.service_providers.speech_service import SynthesizedAudio

logger = logging.getLogger("ocs.channels")


# ---------------------------------------------------------------------------
# ResponseSendingStage
# ---------------------------------------------------------------------------


class ResponseSendingStage(ProcessingStage):
    """TERMINAL STAGE: Sends the response to the user.

    This is the ONLY stage that sends messages to the user.
    Handles both early exit responses and normal bot responses.

    Wrapper methods (_send_text, _send_voice) are decorated with
    @notify_on_delivery_failure for in-app notifications on failure.
    The outer try/except catches any exception that propagates past
    the decorator, sets ctx.sending_exception, and never re-raises.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.formatted_message is not None or ctx.early_exit_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        try:
            if ctx.early_exit_response:
                self._send_text(ctx, ctx.early_exit_response, ctx.participant_identifier)
                return

            # Normal path -- send formatted bot response
            if ctx.voice_audio:
                self._send_voice(ctx, ctx.voice_audio, ctx.participant_identifier)
                if ctx.additional_text_message:
                    self._send_text(ctx, ctx.additional_text_message, ctx.participant_identifier)
            else:
                self._send_text(ctx, ctx.formatted_message, ctx.participant_identifier)

            # Send supported file attachments
            for file in ctx.files_to_send:
                try:
                    ctx.sender.send_file(file, ctx.participant_identifier, ctx.experiment_session.id)
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
                    self._send_text(ctx, download_link, ctx.participant_identifier)
        except Exception as e:
            # Catch-all for send failures -- never propagate
            ctx.sending_exception = e
            ctx.processing_errors.append(f"Send failed: {e}")

    def _send_text(self, ctx: MessageProcessingContext, text: str, recipient: str) -> None:
        try:
            ctx.sender.send_text(text, recipient)
        except Exception as e:
            logger.exception(e)
            message_delivery_failure_notification(
                ctx.experiment,
                session=ctx.experiment_session,
                platform_title=ctx.experiment_channel.platform_enum.title(),
                context="text message",
            )
            raise

    def _send_voice(self, ctx: MessageProcessingContext, audio: SynthesizedAudio, recipient: str) -> None:
        try:
            ctx.sender.send_voice(audio, recipient)
        except Exception as e:
            logger.exception(e)
            message_delivery_failure_notification(
                ctx.experiment,
                session=ctx.experiment_session,
                platform_title=ctx.experiment_channel.platform_enum.title(),
                context="voice message",
            )
            raise


# ---------------------------------------------------------------------------
# SendingErrorHandlerStage
# ---------------------------------------------------------------------------


class SendingErrorHandlerStage(ProcessingStage):
    """TERMINAL STAGE: Handles platform-specific side effects from send failures.

    Inspects ctx.sending_exception for platform-specific errors that require
    action beyond logging (e.g., Telegram 403 "bot was blocked" -> revoke
    participant consent).

    Non-actionable exceptions are ignored (already logged by ResponseSendingStage).
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.sending_exception is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        self._handle_exception(ctx, ctx.sending_exception)

    def _handle_exception(self, ctx: MessageProcessingContext, exc: Exception) -> None:
        """Handle platform-specific sending exceptions."""
        if isinstance(exc, ApiTelegramException):
            if exc.error_code == 403 and "bot was blocked by the user" in exc.description:
                try:
                    participant_data = ParticipantData.objects.get(
                        participant__identifier=ctx.participant_identifier,
                        experiment=ctx.experiment,
                    )
                    participant_data.update_consent(False)
                except ParticipantData.DoesNotExist:
                    ctx.processing_errors.append("Participant data not found during consent revocation")
        # Other platform-specific exception handling can be added here


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

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.participant_allowed and (
            ctx.early_exit_response is not None or ctx.voice_audio is not None or bool(ctx.human_message_tags)
        )

    RESET_COMMAND = "/reset"

    def _is_reset_command(self, ctx: MessageProcessingContext) -> bool:
        """Check if the user's inbound message was the /reset command."""
        return (
            ctx.message is not None
            and ctx.message.content_type == MESSAGE_TYPES.TEXT
            and ctx.message.message_text.lower().strip() == self.RESET_COMMAND
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        if not ctx.experiment_session:
            return

        # Skip all persistence for /reset -- matching current behavior
        # where the reset command is intentionally not recorded.
        if self._is_reset_command(ctx):
            return

        # 1. Apply human message tags set by earlier stages
        if ctx.human_message and ctx.human_message_tags:
            for tag_name, tag_category in ctx.human_message_tags:
                ctx.human_message.create_and_add_tag(tag_name, ctx.experiment.team, tag_category)

        # 2. Persist early exit response to chat history.
        #    Skip when ctx.bot_response exists -- bot.process_input() already
        #    persisted the AI message (e.g. seed message in ConsentFlowStage).
        if ctx.early_exit_response is not None and ctx.bot_response is None:
            ChatMessage.objects.create(
                chat=ctx.experiment_session.chat,
                message_type=ChatMessageType.AI,
                content=ctx.early_exit_response,
            )

        # 3. Tag and save voice attachment on bot response
        if ctx.voice_audio is not None and ctx.bot_response is not None:
            ctx.bot_response.create_and_add_tag("voice", ctx.experiment.team, TagCategories.MEDIA_TYPE)
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
                session.experiment_versions = current_versions + [version_number]
                update_fields.append("experiment_versions")

        session.save(update_fields=update_fields)
