import uuid

from django.conf import settings
from django.db import models
from django.db.models import JSONField, Q
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.experiments import model_audit_fields
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.experiments.models import Experiment
from apps.teams.models import Team
from apps.utils.models import BaseModel
from apps.web.meta import absolute_url

WEB = "web"
TELEGRAM = "telegram"
WHATSAPP = "whatsapp"
FACEBOOK = "facebook"
SUREADHERE = "sureadhere"


class ChannelPlatform(models.TextChoices):
    TELEGRAM = "telegram", "Telegram"
    WEB = "web", "Web"
    WHATSAPP = "whatsapp", "WhatsApp"
    FACEBOOK = "facebook", "Facebook"
    SUREADHERE = "sureadhere", "SureAdhere"
    API = "api", "API"
    SLACK = "slack", "Slack"

    @classmethod
    def for_dropdown(cls, used_platforms, team) -> dict:
        """Returns a dictionary of available platforms for this team. Available platforms will have a `True` value"""
        from apps.service_providers.models import MessagingProvider

        all_platforms = cls.as_list(exclude=[cls.API, cls.WEB])
        platform_availability = {platform: False for platform in all_platforms}
        platform_availability[cls.TELEGRAM] = True

        for provider in MessagingProvider.objects.filter(team=team):
            for platform in provider.get_messaging_service().supported_platforms:
                platform_availability[platform] = True

        if not settings.SLACK_ENABLED:
            platform_availability.pop(cls.SLACK)

        # Platforms already used should not be displayed
        for platform in used_platforms:
            platform_availability.pop(platform)

        return platform_availability

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
                return forms.FacebookChannelForm(channel=channel, *args, **kwargs)
            case self.SUREADHERE:
                return forms.SureAdhereChannelForm(channel=channel, *args, **kwargs)
            case self.SLACK:
                return forms.SlackChannelForm(*args, **kwargs)

    @property
    def channel_identifier_key(self) -> str:
        match self:
            case self.TELEGRAM:
                return "bot_token"
            case self.WHATSAPP:
                return "number"
            case self.FACEBOOK:
                return "page_id"
            case self.SUREADHERE:
                return "sureadhere_tenant_id"
            case self.SLACK:
                return "slack_channel_id"

    @staticmethod
    def as_list(exclude: list["ChannelPlatform"]) -> list["ChannelPlatform"]:
        return [ChannelPlatform(value) for value in ChannelPlatform.values if value not in exclude]


class ExperimentChannelObjectManager(AuditingManager):
    def filter_extras(self, team_slug: str, platform: ChannelPlatform, key: str, value: str):
        extra_data_filter = Q(extra_data__contains={key: value})
        return self.filter(extra_data_filter).filter(experiment__team__slug=team_slug, platform=platform)

    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)

    def get_unfiltered_queryset(self):
        return super().get_queryset()


@audit_fields(*model_audit_fields.EXPERIMENT_CHANNEL_FIELDS, audit_special_queryset_writes=True)
class ExperimentChannel(BaseModel):
    objects = ExperimentChannelObjectManager()
    RESET_COMMAND = "/reset"

    name = models.CharField(max_length=255, help_text="The name of this channel")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, null=True, blank=True)
    deleted = models.BooleanField(default=False)
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
        return f"Channel: {self.name} ({self.platform})"

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
        elif provider_type == MessagingProviderType.sureadhere:
            uri = reverse(
                "channels:new_sureadhere_message",
                kwargs={"sureadhere_tenant_id": self.extra_data.get("sureadhere_tenant_id", "")},
            )
        return absolute_url(
            uri,
            is_secure=True,
        )

    def soft_delete(self):
        self.deleted = True
        self.save()
