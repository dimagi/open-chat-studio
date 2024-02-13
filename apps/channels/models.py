import logging
import uuid

from django.conf import settings
from django.db import models
from django.db.models import JSONField, Q
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from field_audit import audit_fields
from field_audit.models import AuditingManager
from telebot import TeleBot, apihelper, types

from apps.experiments import model_audit_fields
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.experiments.models import Experiment
from apps.teams.models import Team
from apps.teams.utils import get_current_team
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

    def form(self, team: Team):
        from apps.channels.forms import ChannelForm

        return ChannelForm(initial={"platform": self}, team=team)

    def extra_form(self, *args, **kwargs):
        from apps.channels import forms

        channel = kwargs.pop("channel", None)

        match self:
            case self.TELEGRAM:
                return forms.TelegramChannelForm(*args, **kwargs)
            case self.WHATSAPP:
                return forms.WhatsappChannelForm(channel=channel, *args, **kwargs)
            case self.FACEBOOK:
                team_slug = get_current_team().slug
                webhook_url = absolute_url(
                    reverse("channels:new_facebook_message", kwargs={"team_slug": team_slug}), is_secure=True
                )
                initial = kwargs.get("initial", {})
                initial.setdefault("verify_token", str(uuid.uuid4()))
                initial.setdefault("webook_url", webhook_url)
                kwargs["initial"] = initial
                return forms.FacebookChannelForm(*args, **kwargs)

    @property
    def channel_identifier_key(self) -> str:
        match self:
            case self.TELEGRAM:
                return "bot_token"
            case self.WHATSAPP:
                return "number"
            case self.FACEBOOK:
                return "page_id"


class ExperimentChannelObjectManager(AuditingManager):
    def filter_extras(self, team_slug: str, platform: ChannelPlatform, key: str, value: str):
        extra_data_filter = Q(extra_data__contains={key: value})
        return self.filter(extra_data_filter).filter(experiment__team__slug=team_slug, platform=platform)


@audit_fields(*model_audit_fields.EXPERIMENT_CHANNEL_FIELDS, audit_special_queryset_writes=True)
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
    messaging_provider = models.ForeignKey(
        "service_providers.MessagingProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Messaging Provider",
    )

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

        return ChannelForm(instance=self, team=self.experiment.team, *args, **kwargs)

    def extra_form(self, *args, **kwargs):
        return self.platform_enum.extra_form(initial=self.extra_data, channel=self, *args, **kwargs)

    @staticmethod
    def check_usage_by_another_experiment(platform: ChannelPlatform, identifier: str, new_experiment: Experiment):
        """
        Checks if another experiment (one that is not the same as `new_experiment`) already uses the channel specified
        by its `identifier` and `platform`. Raises `ChannelAlreadyUtilizedException` error when another
        experiment uses it.
        """

        filter_params = {f"extra_data__{platform.channel_identifier_key}": identifier}
        channel = ExperimentChannel.objects.filter(**filter_params).first()
        if channel and channel.experiment != new_experiment:
            url = reverse(
                "experiments:single_experiment_home",
                kwargs={"team_slug": channel.experiment.team.slug, "experiment_id": channel.experiment.id},
            )
            raise ChannelAlreadyUtilizedException(
                format_html(_("This channel is already used in <a href={}><u>another experiment</u></a>"), url)
            )

    @property
    def webhook_url(self) -> str:
        """The wehook URL that should be used in external services"""
        from apps.service_providers.models import MessagingProviderType

        if not self.messaging_provider:
            return
        uri = ""
        provider_type = self.messaging_provider.type
        if provider_type == MessagingProviderType.twilio:
            uri = reverse("channels:new_twilio_message")
        elif provider_type == MessagingProviderType.turnio:
            uri = reverse("channels:new_turn_message", kwargs={"experiment_id": self.experiment.public_id})
        return absolute_url(
            uri,
            is_secure=True,
        )


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
