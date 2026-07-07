from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

import httpx
import requests
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.util import antiflood, smart_split

from apps.channels import audio
from apps.channels.callbacks import ChannelCallbacks
from apps.channels.capabilities import ChannelCapabilities, PlatformConsentConfig
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.pipeline import MessageProcessingContext
from apps.channels.const import MESSAGE_TYPES
from apps.channels.datamodels import TelegramMessage
from apps.channels.sender import ChannelSender
from apps.channels.stages.terminal import DeliveryErrorHandler, MessageDeliveryFailure
from apps.experiments.models import ParticipantData
from apps.service_providers.file_limits import can_send_on_telegram

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.speech_service import SynthesizedAudio

logger = logging.getLogger("ocs.channels")


class TelegramCallbacks(ChannelCallbacks):
    """Telegram-specific lifecycle callbacks."""

    def __init__(self, telegram_bot: TeleBot):
        self.telegram_bot = telegram_bot

    def _safe_send_chat_action(self, recipient: str, action: str) -> None:
        try:
            self.telegram_bot.send_chat_action(chat_id=recipient, action=action)
        except (requests.exceptions.RequestException, ConnectionError) as e:
            logger.warning("Failed to send chat action '%s' to %s: %s", action, recipient, e)

    def transcription_started(self, recipient: str) -> None:
        self._safe_send_chat_action(recipient, "upload_voice")

    def on_submit_input_to_llm(self, recipient: str) -> None:
        self._safe_send_chat_action(recipient, "typing")

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        # Telegram supports reply-to threading via the inbound message id, but
        # the recipient-only callback signature does not carry it.  Sending the
        # transcript as a plain text message preserves the user-visible behavior.
        self.telegram_bot.send_message(recipient, text=f"I heard: {transcript}")

    def get_message_audio(self, message: BaseMessage) -> BytesIO:
        assert isinstance(message, TelegramMessage), "TelegramCallbacks requires a TelegramMessage"
        file_url = self.telegram_bot.get_file_url(message.media_id)
        response = httpx.get(file_url, timeout=30.0)
        response.raise_for_status()
        ogg_audio = BytesIO(response.content)
        return audio.convert_audio(ogg_audio, target_format="wav", source_format="ogg")


class TelegramSender(ChannelSender):
    """Delivers text, voice, and file messages over the Telegram Bot API."""

    def __init__(self, telegram_bot: TeleBot):
        self.telegram_bot = telegram_bot

    def send_text(self, text: str, recipient: str) -> None:
        for chunk in smart_split(text):
            antiflood(self.telegram_bot.send_message, recipient, text=chunk)

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        antiflood(
            self.telegram_bot.send_voice,
            recipient,
            voice=audio.audio,
            duration=audio.duration,
        )

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        mime = file.content_type or ""
        main_type = mime.split("/")[0] if mime else ""
        match main_type:
            case "image":
                method = self.telegram_bot.send_photo
                arg_name = "photo"
            case "video":
                method = self.telegram_bot.send_video
                arg_name = "video"
            case "audio":
                method = self.telegram_bot.send_audio
                arg_name = "audio"
            case _:
                method = self.telegram_bot.send_document
                arg_name = "document"

        antiflood(method, recipient, **{arg_name: file.file})


class TelegramChannel(ChannelBase):
    """Message handler for Telegram.

    Full pipeline with voice, file, and conversational consent support.
    Telegram-specific behavior is isolated to TelegramSender, TelegramCallbacks,
    and the 403 "bot was blocked" handling in SendingErrorHandlerStage.
    """

    voice_replies_supported = True
    supports_multimedia = True
    supported_message_types = (MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE)

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self.telegram_bot = TeleBot(self.experiment_channel.extra_data["bot_token"], threaded=False)

    def _get_callbacks(self) -> ChannelCallbacks:
        return TelegramCallbacks(self.telegram_bot)

    def _get_sender(self) -> ChannelSender:
        return TelegramSender(self.telegram_bot)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=self.supports_multimedia,
            supports_conversational_consent=True,
            supported_message_types=self.supported_message_types,
            can_send_file=self._can_send_file,
            # Lenient: only block when consent was explicitly revoked (e.g.
            # handle_telegram_block sets consent=False after a 403). Participants
            # with no ParticipantData row or no consent key are allowed through.
            consent_config=PlatformConsentConfig(strict=False, default_consent=True),
        )

    def _can_send_file(self, file: File) -> bool:
        return can_send_on_telegram(file.content_type or "", file.content_size or 0).supported

    def _get_delivery_error_handlers(self) -> list[DeliveryErrorHandler]:
        return [handle_telegram_block]


def handle_telegram_block(ctx: MessageProcessingContext, exc: Exception) -> bool:
    """Revoke participant consent when Telegram reports the bot was blocked.

    Returns True if the exception was a recognized "bot blocked" 403; False otherwise.
    """
    if not isinstance(exc, MessageDeliveryFailure) or not isinstance(exc.original_exc, ApiTelegramException):
        return False
    api_exc = exc.original_exc
    if api_exc.error_code != 403 or "bot was blocked by the user" not in api_exc.description:
        return False
    try:
        participant_data = ParticipantData.objects.for_experiment(ctx.experiment).get(
            participant__identifier=ctx.participant_identifier,
        )
        participant_data.update_consent(False)
    except ParticipantData.DoesNotExist:
        ctx.processing_errors.append("Participant data not found during consent revocation")
    return True
