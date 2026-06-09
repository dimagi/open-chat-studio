from typing import Protocol

from django.conf import settings
from telebot import TeleBot, types

from apps.channels.models import ExperimentChannel


class WebhookManager(Protocol):
    """Configures a channel's inbound message webhook at the upstream provider.

    Satisfied structurally by both MessagingService (provider-backed platforms) and
    TelegramWebhookManager (per-channel bot token).
    """

    supports_webhook_management: bool

    def set_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None: ...

    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None: ...


class TelegramWebhookManager:
    """Manages the Telegram bot webhook using the per-channel bot token in extra_data."""

    supports_webhook_management = True

    def set_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None:
        bot = TeleBot(extra_data.get("bot_token", ""), threaded=False)
        bot.set_webhook(webhook_url, secret_token=settings.TELEGRAM_SECRET_TOKEN)
        bot.set_my_commands(commands=[types.BotCommand(ExperimentChannel.RESET_COMMAND, "Restart chat")])

    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None:
        bot = TeleBot(extra_data.get("bot_token", ""), threaded=False)
        bot.set_webhook(None)
