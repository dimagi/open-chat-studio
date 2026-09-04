from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import JSONObject
from django.urls import reverse
from django.utils import timezone

from apps.teams.models import BaseTeamModel
from apps.utils.fields import SanitizedJSONField


class LevelChoices(models.IntegerChoices):
    INFO = 0, "Info"
    WARNING = 1, "Warning"
    ERROR = 2, "Error"


class UserNotificationPreferences(BaseTeamModel):
    """Store user preferences for in-app and email notifications"""

    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE, related_name="notification_preferences")

    # In-app notification preferences
    in_app_enabled = models.BooleanField(default=True)
    in_app_level = models.PositiveSmallIntegerField(
        choices=LevelChoices.choices,
        default=LevelChoices.INFO,
    )

    # Email notification preferences
    email_enabled = models.BooleanField(default=False)
    email_level = models.PositiveSmallIntegerField(
        choices=LevelChoices.choices,
        default=LevelChoices.WARNING,
    )
    do_not_disturb_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "User Notification Preferences"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "team"],
                name="unique_notification_preferences_per_user_and_team",
            ),
        ]

    def __str__(self):
        return f"Notification preferences for {self.user}"


class EventType(BaseTeamModel):
    """
    When a notification is created, we should get or create an EventType with the same identifier.
    A NotificationEvent should be created also, each time, regardless if the EventType is new or not.
    For each user to be notified, we get or create an EventUser with the same event_type and user.
    This allows the user to manage an event (mark as read, mute, etc.) without affecting other users that should also be
    notified about the same event.
    """

    identifier = models.CharField(max_length=40)
    event_data = SanitizedJSONField(default=dict, blank=True)
    level = models.PositiveSmallIntegerField(choices=LevelChoices.choices, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "identifier"],
                condition=~models.Q(identifier=""),
                name="unique_event_type_per_team_and_identifier",
            ),
        ]
        indexes = [
            models.Index(fields=["created_at"], name="eventtype_created_at_idx"),
        ]


class NotificationEvent(BaseTeamModel):
    event_type = models.ForeignKey(EventType, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    message = models.TextField()
    links = SanitizedJSONField(null=True)


class EventUserQuerySet(models.QuerySet):
    def with_latest_event(self):
        latest_event_subquery = NotificationEvent.objects.filter(
            event_type=models.OuterRef("event_type"),
            team=models.OuterRef("team"),
        ).order_by("-created_at")[:1]
        return self.annotate(
            latest_event=models.Subquery(
                latest_event_subquery.values(
                    data=JSONObject(
                        title="title",
                        created_at=models.F("created_at"),
                    )
                )
            ),
            latest_event_created_at=models.Subquery(
                latest_event_subquery.values("created_at"),
                output_field=models.DateTimeField(),
            ),
        )

    def with_mute_status(self) -> models.QuerySet:
        return self.annotate(
            is_muted=models.Case(
                models.When(muted_until__gt=timezone.now(), then=True),
                default=False,
                output_field=models.BooleanField(),
            )
        )

    def with_event_count(self) -> models.QuerySet:
        """Annotate how many times this event has recurred: the number of `NotificationEvent`
        rows under the same `EventType`, at any level -- a repeating Info or Warning is as
        much a signal worth surfacing as a repeating error."""
        count_subquery = (
            NotificationEvent.objects.filter(
                event_type=models.OuterRef("event_type"),
                team=models.OuterRef("team"),
            )
            .order_by()
            .values("event_type")
            .annotate(count=models.Count("id"))
            .values("count")
        )
        return self.annotate(event_count=models.Subquery(count_subquery, output_field=models.IntegerField()))


class EventUserManager(models.Manager.from_queryset(EventUserQuerySet)):
    pass


class NotificationChannel(BaseTeamModel):
    """A team-level Slack channel notifications are posted to."""

    messaging_provider = models.ForeignKey(
        "service_providers.MessagingProvider",
        on_delete=models.CASCADE,
        related_name="notification_channels",
    )
    channel_name = models.CharField(max_length=255)
    channel_id = models.CharField(  # noqa: DJ001 - nullable so the AddField migration won't break the running release
        max_length=255,
        null=True,
        blank=True,
        help_text="Resolved Slack channel ID (falls back to the channel name).",
    )
    level = models.PositiveSmallIntegerField(
        choices=LevelChoices.choices,
        default=LevelChoices.WARNING,
    )
    enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Notification channels"
        ordering = ("channel_name",)
        constraints = [
            models.UniqueConstraint(
                fields=["team", "messaging_provider", "level"],
                name="unique_notification_channel_per_team_provider_and_level",
            ),
        ]

    def __str__(self):
        return f"Notifications to {self.channel_name}"

    def clean(self):
        from apps.service_providers.models import MessagingProviderType  # noqa: PLC0415 - circular import avoided

        super().clean()
        if not self.messaging_provider_id:
            return
        provider = self.messaging_provider
        if provider.team_id != self.team_id or provider.type != MessagingProviderType.slack:
            raise ValidationError(
                {"messaging_provider": "The Slack workspace must belong to this team and be a Slack provider."}
            )


class EventUser(BaseTeamModel):
    event_type = models.ForeignKey(EventType, on_delete=models.CASCADE)
    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE)
    read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True)
    objects = EventUserManager()
    muted_until = models.DateTimeField(null=True, help_text="When the mute expires")

    class Meta:
        unique_together = ("event_type", "user")

    def get_absolute_url(self):
        return reverse(
            "ocs_notifications:notification_event_home",
            args=[self.event_type_id],
        )
