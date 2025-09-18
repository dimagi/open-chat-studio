import uuid
from typing import Self

from django.conf import settings
from django.db import models
from django.db.models import JSONField, Q
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.experiments import model_audit_fields
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.experiments.models import Experiment
from apps.teams.models import BaseTeamModel, Flag
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
    COMMCARE_CONNECT = "commcare_connect", "CommCare Connect"
    EVALUATIONS = "evaluations", "Evaluations"
    EMBEDDED_WIDGET = "embedded_widget", "Embedded Widget"

    @classmethod
    def team_global_platforms(cls):
        """These platforms should only ever have one channel per team"""
        return [cls.API, cls.WEB, cls.EVALUATIONS]

    @classmethod
    def for_dropdown(cls, used_platforms, team) -> dict[Self, bool]:
        """Returns a dictionary of available platforms for this team. Available platforms will have a `True` value"""
        from apps.service_providers.models import MessagingProvider

        all_platforms = cls.as_list(exclude=[cls.API, cls.WEB, cls.EVALUATIONS])
        platform_availability = {platform: False for platform in all_platforms}
        platform_availability[cls.TELEGRAM] = True
        platform_availability[cls.EMBEDDED_WIDGET] = True

        for provider in MessagingProvider.objects.filter(team=team):
            for platform in provider.get_messaging_service().supported_platforms:
                platform_availability[platform] = True

        if not settings.SLACK_ENABLED:
            platform_availability.pop(cls.SLACK)

        flag = Flag.get("flag_commcare_connect")
        commcare_connect_flag_enabled = flag.is_active_for_team(team)
        if not commcare_connect_flag_enabled:
            platform_availability.pop(cls.COMMCARE_CONNECT)
        elif settings.COMMCARE_CONNECT_ENABLED:
            platform_availability[cls.COMMCARE_CONNECT] = True

        # Platforms already used should not be displayed
        for platform in used_platforms:
            platform_availability.pop(platform)

        return platform_availability

    def form(self, experiment: Experiment):
        from apps.channels.forms import ChannelForm

        return ChannelForm(initial={"platform": self}, experiment=experiment)

    def extra_form(self, **kwargs):
        from apps.channels import forms

        match self:
            case self.TELEGRAM:
                return forms.TelegramChannelForm(**kwargs)
            case self.WHATSAPP:
                return forms.WhatsappChannelForm(**kwargs)
            case self.FACEBOOK:
                return forms.FacebookChannelForm(**kwargs)
            case self.SUREADHERE:
                return forms.SureAdhereChannelForm(**kwargs)
            case self.SLACK:
                return forms.SlackChannelForm(**kwargs)
            case self.COMMCARE_CONNECT:
                return forms.CommCareConnectChannelForm(**kwargs)
            case self.EMBEDDED_WIDGET:
                exp = kwargs.pop("experiment", None)
                return forms.EmbeddedWidgetChannelForm(experiment=exp, **kwargs)
        return None

    @property
    def channel_identifier_key(self) -> str | None:
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
                # handled by the slack form directly
                return None
            case self.COMMCARE_CONNECT:
                # The bot_name will be shown to the user, which is how they'll know which bot it is. We use the bot name
                # here to prevent other bots from using the same name in order to mitigate confusion.
                return "commcare_connect_bot_name"
            case self.EMBEDDED_WIDGET:
                return "widget_token"
        return None

    @staticmethod
    def as_list(exclude: list["ChannelPlatform"]) -> list["ChannelPlatform"]:
        return [ChannelPlatform(value) for value in ChannelPlatform.values if value not in exclude]

    @classmethod
    def for_filter(cls, team) -> list[str]:
        platforms = cls.for_dropdown([], team).keys()
        platforms_with_labels = [platform.label for platform in platforms]
        platforms_with_labels.append(cls.API.label)
        platforms_with_labels.append(cls.WEB.label)
        platforms_with_labels.append(cls.EVALUATIONS.label)
        return sorted(platforms_with_labels)


class ExperimentChannelObjectManager(AuditingManager):
    def filter_extras(self, team_slug: str, platform: ChannelPlatform, key: str, value: str):
        extra_data_filter = Q(extra_data__contains={key: value})
        return self.filter(extra_data_filter).filter(experiment__team__slug=team_slug, platform=platform)

    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)

    def get_unfiltered_queryset(self):
        return super().get_queryset()

    def get_team_api_channel(self, team):
        channel, _ = self.get_or_create(team=team, platform=ChannelPlatform.API, name=f"{team.slug}-api-channel")
        return channel

    def get_team_web_channel(self, team):
        channel, _ = self.get_or_create(team=team, platform=ChannelPlatform.WEB, name=f"{team.slug}-web-channel")
        return channel

    def get_team_evaluations_channel(self, team):
        channel, _ = self.get_or_create(
            team=team, platform=ChannelPlatform.EVALUATIONS, name=f"{team.slug}-evaluations-channel"
        )
        return channel


@audit_fields(*model_audit_fields.EXPERIMENT_CHANNEL_FIELDS, audit_special_queryset_writes=True)
class ExperimentChannel(BaseTeamModel):
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
        db_table = "channels_experimentchannel"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=("team", "platform"),
                name="unique_global_channel_per_team",
                condition=Q(platform__in=ChannelPlatform.team_global_platforms(), deleted=False),
            ),
        ]

    def __str__(self):
        return f"Channel: {self.name} ({self.platform})"

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = self.experiment.name
        return super().save(*args, **kwargs)

    @property
    def platform_enum(self):
        return ChannelPlatform(self.platform)

    def extra_form(self, experiment, data: dict = None):
        if not experiment.id == self.experiment_id:
            raise ValueError("Experiment ID does not match channel experiment ID")
        return self.platform_enum.extra_form(experiment=experiment, channel=self, initial=self.extra_data, data=data)

    @staticmethod
    def check_usage_by_another_experiment(platform: ChannelPlatform, identifier: str, new_experiment: Experiment):
        """
        Checks if another experiment (one that is not the same as `new_experiment`) already uses the channel specified
        by its `identifier` and `platform`. Raises `ChannelAlreadyUtilizedException` error when another
        experiment uses it.
        """
        filter_params = {f"extra_data__{platform.channel_identifier_key}": identifier}
        existing_channels = (
            ExperimentChannel.objects.filter(**filter_params, platform=platform, deleted=False)
            .exclude(experiment=new_experiment)
            .select_related("team")
        )

        channel = existing_channels.first()
        if channel:
            if channel.team_id == new_experiment.team_id:
                raise ChannelAlreadyUtilizedException(ChannelAlreadyUtilizedException.get_message_for_channel(channel))
            raise ChannelAlreadyUtilizedException()

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
