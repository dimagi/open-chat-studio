import logging
import uuid

from django.conf import settings
from django.db import models
from django.db.models import JSONField
from django.urls import reverse
from telebot import TeleBot, apihelper, types

from apps.experiments.models import Experiment, ExperimentSession
from apps.utils.models import BaseModel
from apps.web.meta import absolute_url

WEB = "web"
TELEGRAM = "telegram"
WHATSAPP = "whatsapp"


class ExperimentChannel(BaseModel):
    RESET_COMMAND = "/reset"
    PLATFORM = ((TELEGRAM, "Telegram"), (WEB, "Web"), (WHATSAPP, "WhatsApp"))

    name = models.CharField(max_length=40, help_text="The name of this channel")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, null=True, blank=True)
    active = models.BooleanField(default=True)
    extra_data = JSONField(default=dict, help_text="Fields needed for channel authorization. Format is JSON")
    external_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    platform = models.CharField(max_length=32, choices=PLATFORM, default="telegram")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"name: {self.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        try:
            if self.platform == TELEGRAM:
                _set_telegram_webhook(self)
        except apihelper.ApiTelegramException:
            token = self.extra_data.get("bot_token", "")
            logging.error(f"Unable set Telegram webhook with token '{token}'")


def _set_telegram_webhook(experiment_channel: ExperimentChannel):
    """
    Set the webhook at Telegram to allow message forwarding to this platform
    """
    tele_bot = TeleBot(experiment_channel.extra_data.get("bot_token", ""), threaded=False)
    if experiment_channel.active:
        webhook_url = absolute_url(reverse("channels:new_telegram_message", args=[experiment_channel.external_id]))
    else:
        webhook_url = None

    tele_bot.set_webhook(webhook_url, secret_token=settings.TELEGRAM_SECRET_TOKEN)
    tele_bot.set_my_commands(commands=[types.BotCommand(ExperimentChannel.RESET_COMMAND, "Restart chat")])


# TODO: Remove this model
class ChannelSession(BaseModel):
    external_chat_id = models.CharField(null=False, blank=False)
    experiment_channel = models.ForeignKey(
        ExperimentChannel, on_delete=models.CASCADE, related_name="channel_sessions", null=True, blank=True
    )
    experiment_session = models.OneToOneField(
        ExperimentSession, on_delete=models.CASCADE, related_name="channel_session"
    )
