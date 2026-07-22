import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Self, cast

from django.conf import settings
from django.db import models
from django.db.models import JSONField, Q
from django.urls import reverse
from django.utils import timezone
from field_audit import audit_fields
from field_audit.models import AuditAction, AuditingManager

from apps.channels import widget_versions
from apps.experiments import model_audit_fields
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.experiments.models import Experiment
from apps.teams.models import BaseTeamModel, Flag
from apps.web.meta import absolute_url

if TYPE_CHECKING:
    from apps.channels.webhooks import WebhookManager

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
    EMAIL = "email", "Email"

    @classmethod
    def team_global_platforms(cls):
        """These platforms should only ever have one channel per team"""
        return [cls.API, cls.WEB, cls.EVALUATIONS]

    @classmethod
    def for_dropdown(cls, used_platforms, team) -> dict[Self, bool]:
        """Returns a dictionary of available platforms for this team. Available platforms will have a `True` value"""
        from apps.service_providers.models import (  # noqa: PLC0415 - circular: service_providers.models imports channels.models
            MessagingProvider,
        )

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

        flag = Flag.get("flag_email_channel")
        email_flag_enabled = flag.is_active_for_team(team)
        if not email_flag_enabled or not settings.EMAIL_CHANNEL_ALLOWED_DOMAINS:
            platform_availability.pop(cls.EMAIL, None)
        else:
            platform_availability[cls.EMAIL] = True

        # Platforms already used should not be displayed
        for platform in used_platforms:
            platform_availability.pop(platform)

        return cast(dict[Self, bool], platform_availability)

    def form(self, experiment: Experiment):
        from apps.channels.forms import ChannelForm  # noqa: PLC0415 - circular: channels.forms imports channels.models

        return ChannelForm(initial={"platform": self}, experiment=experiment)

    def extra_form(self, **kwargs):
        from apps.channels import forms  # noqa: PLC0415 - circular: channels.forms imports channels.models

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
                return forms.EmbeddedWidgetChannelForm(**kwargs)
            case self.EMAIL:
                return forms.EmailChannelForm(**kwargs)
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
            case self.EMAIL:
                return "email_address"
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

    def normalize_identifier(self, identifier: str) -> str:
        match self:
            case self.COMMCARE_CONNECT:
                return identifier.lower()
        return identifier


class WidgetAuthLevel(models.IntegerChoices):
    """Minimum authentication a chat widget channel requires from the client.

    Only meaningful for EMBEDDED_WIDGET channels. New channels default to the
    strictest level (SESSION_TOKEN); existing channels are migrated to the level
    matching the widget version they last reported. See
    docs/developer_guides/widget_versioning.md and issue #3858.
    """

    NONE = 0, "None (pre-0.5.1 legacy)"
    EMBED_KEY = 1, "Embed key only (0.5.1 – 0.8.x)"
    SESSION_TOKEN = 2, "Session token required (0.9.0+)"


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
    WIDGET_VERSION_REFRESH_INTERVAL = timedelta(hours=24)
    # Grace period between notifying a team of a pending required_auth_level increase
    # and actually applying it (see apps.channels.tasks.ratchet_widget_auth_levels).
    AUTH_LEVEL_RATCHET_GRACE = timedelta(days=14)

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
    # Telemetry reported by the embedded widget; deliberately excluded from
    # EXPERIMENT_CHANNEL_FIELDS auditing (written from the request path).
    widget_version = models.CharField(max_length=32, null=True, blank=True)  # noqa: DJ001
    widget_version_updated_at = models.DateTimeField(null=True, blank=True)
    # Durable per-channel auth policy for embedded widgets. New channels default to the
    # strictest level; the request path enforces it rather than inferring a mode from
    # per-request heuristics. Only meaningful for EMBEDDED_WIDGET channels.
    required_auth_level = models.PositiveSmallIntegerField(
        choices=WidgetAuthLevel.choices,
        default=WidgetAuthLevel.SESSION_TOKEN,
        help_text=(
            "Minimum authentication an embedded widget must provide. New widgets (0.9.0+) send a "
            "session token; only downgrade this for a channel you know is running an older widget."
        ),
    )
    # Set when a widget upgrade is detected: the level required_auth_level will be raised to
    # once the team has been notified and the grace period elapses. Workflow state written by
    # ratchet_widget_auth_levels; not audited (the audited change is required_auth_level itself).
    pending_auth_level = models.PositiveSmallIntegerField(choices=WidgetAuthLevel.choices, null=True, blank=True)
    auth_level_notified_at = models.DateTimeField(null=True, blank=True)

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

    @property
    def widget_update_status(self) -> widget_versions.WidgetUpdateStatus | None:
        if self.platform_enum != ChannelPlatform.EMBEDDED_WIDGET:
            return None
        return widget_versions.get_widget_update_status(self.widget_version)

    @property
    def widget_auth_level(self) -> "WidgetAuthLevel | None":
        """The required auth level for embedded widget channels, or None for other platforms.

        `required_auth_level` is only meaningful for EMBEDDED_WIDGET channels; every other
        platform returns None so callers fall back to their non-widget behaviour.
        """
        if self.platform_enum != ChannelPlatform.EMBEDDED_WIDGET:
            return None
        return WidgetAuthLevel(self.required_auth_level)

    @property
    def min_widget_version(self) -> str | None:
        """Minimum widget version required by this channel's current auth level.

        None for non-widget channels or a NONE-level widget channel (no floor).
        """
        level = self.widget_auth_level
        if level is None:
            return None
        return widget_versions.min_version_for_level(level)

    @property
    def pending_min_widget_version(self) -> str | None:
        """Minimum widget version the pending auth level will require, if a bump is pending."""
        if self.pending_auth_level is None:
            return None
        return widget_versions.min_version_for_level(self.pending_auth_level)

    @property
    def pending_auth_level_effective_at(self):
        """When a pending auth-level increase will be applied, or None if none is pending."""
        if self.auth_level_notified_at is None:
            return None
        return self.auth_level_notified_at + self.AUTH_LEVEL_RATCHET_GRACE

    def record_widget_version(self, raw_version: str | None) -> None:
        """Persist the version reported by the widget (x-ocs-widget-version header).

        Pre-0.5.1 widgets send no header; for those we record a placeholder so the
        deprecation helpers (badge, team notifications) treat the channel as running
        a known-old version rather than an unreported one. A present-but-unparseable
        header is ignored as garbage, and the placeholder never overwrites a real
        version already on record — a transient missing header must not downgrade it.

        Telemetry write: bypasses save() and auditing, throttled to one write
        per 24h unless the version changes.
        """
        version = widget_versions.clean_widget_version(raw_version)
        if version is None:
            if raw_version is not None or self.widget_version is not None:
                return
            version = widget_versions.UNKNOWN_WIDGET_VERSION
        now = timezone.now()
        if self._widget_version_recently_recorded(version, now):
            return
        ExperimentChannel.objects.filter(pk=self.pk).update(
            widget_version=version,
            widget_version_updated_at=now,
            audit_action=AuditAction.IGNORE,
        )

    def _widget_version_recently_recorded(self, version: str, now) -> bool:
        """True if `version` was already recorded within the refresh interval."""
        if version != self.widget_version or not self.widget_version_updated_at:
            return False
        return now - self.widget_version_updated_at < self.WIDGET_VERSION_REFRESH_INTERVAL

    def extra_form(self, experiment, data: dict | None = None):
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
        """The webhook URL that should be used in external services"""
        from apps.service_providers.models import (  # noqa: PLC0415 - circular: service_providers.models imports channels.models
            MessagingProviderType,
        )

        if self.platform == ChannelPlatform.TELEGRAM:
            return absolute_url(reverse("channels:new_telegram_message", args=[self.external_id]), is_secure=True)

        if not self.messaging_provider:
            return ""
        uri = ""
        provider_type = self.messaging_provider.type
        if provider_type == MessagingProviderType.twilio:
            uri = reverse("channels:new_twilio_message")
        elif provider_type == MessagingProviderType.turnio:
            uri = reverse("channels:new_turn_message", kwargs={"experiment_id": self.experiment.public_id})
        elif provider_type == MessagingProviderType.meta_cloud_api:
            uri = reverse("channels:new_meta_cloud_api_message")
        elif provider_type == MessagingProviderType.sureadhere:
            uri = reverse(
                "channels:new_sureadhere_message",
                kwargs={"sureadhere_tenant_id": self.extra_data.get("sureadhere_tenant_id", "")},
            )
        return absolute_url(
            uri,
            is_secure=True,
        )

    def get_webhook_manager(self) -> "WebhookManager | None":
        """Return the object that manages this channel's inbound webhook, or None.

        Telegram uses its per-channel bot token; other provider-backed channels delegate
        to their MessagingService. Both satisfy the WebhookManager protocol structurally.
        Telegram is checked first so it always uses the Telegram API path, regardless of
        any messaging_provider that may be set.
        """
        if self.platform == ChannelPlatform.TELEGRAM:
            from apps.channels.webhooks import (  # noqa: PLC0415 - lazy: avoid importing telebot at module load
                TelegramWebhookManager,
            )

            return TelegramWebhookManager()
        if self.messaging_provider:
            return self.messaging_provider.get_messaging_service()
        return None

    def soft_delete(self):
        self.deleted = True
        self.save()
