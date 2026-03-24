from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.chat.channels import MESSAGE_TYPES

if TYPE_CHECKING:
    from apps.channels.datamodels import BaseMessage
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.speech_service import SynthesizedAudio


class TelegramSender(ChannelSender):
    """Sends messages via the Telegram Bot API.

    Exceptions (e.g., ApiTelegramException) are NOT caught here -- they
    propagate to ResponseSendingStage which catches them, sets
    ctx.sending_exception, and lets SendingErrorHandlerStage handle
    platform-specific side effects (e.g., Telegram 403 consent revocation).
    """

    def __init__(self, telegram_bot):
        self._bot = telegram_bot

    def send_text(self, text: str, recipient: str) -> None:
        from telebot.util import antiflood, smart_split  # noqa: PLC0415

        for chunk in smart_split(text):
            antiflood(self._bot.send_message, recipient, text=chunk)

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        from telebot.util import antiflood  # noqa: PLC0415

        antiflood(
            self._bot.send_voice,
            recipient,
            voice=audio.audio,
            duration=audio.duration,
        )

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        from telebot.util import antiflood  # noqa: PLC0415

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
        import httpx  # noqa: PLC0415

        from apps.channels import audio as audio_utils  # noqa: PLC0415

        file_url = self._bot.get_file_url(message.media_id)
        response = httpx.get(file_url)
        response.raise_for_status()
        ogg_audio = BytesIO(response.content)
        return audio_utils.convert_audio(ogg_audio, target_format="wav", source_format="ogg")


class TelegramChannel(ChannelBase):
    """Telegram channel implementation.

    Full pipeline with voice, files, sender, and callbacks.
    Uses TeleBot directly (no messaging service dependency).
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
        from telebot import TeleBot  # noqa: PLC0415

        self.telegram_bot = TeleBot(experiment_channel.extra_data["bot_token"], threaded=False)
        super().__init__(experiment, experiment_channel, experiment_session)

    def _get_sender(self) -> ChannelSender:
        return TelegramSender(self.telegram_bot)

    def _get_callbacks(self) -> ChannelCallbacks:
        return TelegramCallbacks(sender=TelegramSender(self.telegram_bot), telegram_bot=self.telegram_bot)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=True,
            supports_files=True,
            supports_conversational_consent=True,
            supports_static_triggers=True,
            supported_message_types=(MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE),
            can_send_file=self._can_send_file,
        )

    def _can_send_file(self, file) -> bool:
        mime = file.content_type
        size = file.content_size or 0
        if mime.startswith("image/"):
            return size <= 10 * 1024 * 1024  # 10 MB for images
        elif mime.startswith(("video/", "audio/", "application/")):
            return size <= 50 * 1024 * 1024  # 50 MB for other supported types
        return False
