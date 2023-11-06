import logging
import uuid

from django.conf import settings
from django.db import models
from django.db.models import JSONField, Q
from django.urls import reverse
from telebot import TeleBot, apihelper, types

from apps.experiments.models import Experiment
from apps.utils.models import BaseModel
from apps.web.meta import absolute_url

WEB = "web"
TELEGRAM = "telegram"
WHATSAPP = "whatsapp"
FACEBOOK = "facebook"


class ChannelPlatform(models.TextChoices):
    TELEGRAM = "telegram", "Telegram"
    WEB = "web", "Web"
    WHATSAPP = "whatsapp", "WhatsApp"
    FACEBOOK = "facebook", "Facebook"

    @classmethod
    def for_dropdown(cls):
        return [
            cls.TELEGRAM,
            cls.WHATSAPP,
            cls.FACEBOOK,
        ]

    def form(self):
        from apps.channels.forms import ChannelForm

        return ChannelForm(initial={"platform": self})

    def extra_form(self, *args, **kwargs):
        from apps.channels import forms

        match self:
            case self.TELEGRAM:
                return forms.TelegramChannelForm(*args, **kwargs)
            case self.WHATSAPP:
                return forms.WhatsappChannelForm(*args, **kwargs)
            case self.FACEBOOK:
                initial = kwargs.get("initial", {})
                initial.setdefault("verify_token", str(uuid.uuid4()))
                kwargs["initial"] = initial
                return forms.FacebookChannelForm(*args, **kwargs)


class ExperimentChannelObjectManager(models.Manager):
    def filter_extras(self, key: str, value: str, platform: ChannelPlatform, team: str):
        extra_data_filter = Q(extra_data__contains={key: value})
        return super().get_queryset().filter(extra_data_filter).filter(experiment__team__slug=team, platform=platform)


class ExperimentChannel(BaseModel):
    objects = ExperimentChannelObjectManager()
    RESET_COMMAND = "/reset"
    PLATFORM = ((TELEGRAM, "Telegram"), (WEB, "Web"), (WHATSAPP, "WhatsApp"), (FACEBOOK, "Facebook"))

    name = models.CharField(max_length=40, help_text="The name of this channel")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, null=True, blank=True)
    active = models.BooleanField(default=True)
    extra_data = JSONField(default=dict, help_text="Fields needed for channel authorization. Format is JSON")
    external_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    platform = models.CharField(max_length=32, choices=ChannelPlatform.choices, default="telegram")

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

    @property
    def platform_enum(self):
        return ChannelPlatform(self.platform)

    def form(self, *args, **kwargs):
        from apps.channels.forms import ChannelForm

        return ChannelForm(instance=self, *args, **kwargs)

    def extra_form(self, *args, **kwargs):
        return self.platform_enum.extra_form(initial=self.extra_data, *args, **kwargs)


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
