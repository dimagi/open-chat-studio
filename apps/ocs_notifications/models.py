from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.teams.models import BaseTeamModel
from apps.utils.fields import SanitizedJSONField


class LevelChoices(models.IntegerChoices):
    INFO = 0, "Info"
    WARNING = 1, "Warning"
    ERROR = 2, "Error"


class Notification(BaseTeamModel):
    # TODO: Remove model
    title = models.CharField(max_length=255)
    message = models.TextField()
    level = models.PositiveSmallIntegerField(choices=LevelChoices.choices, db_index=True)
    users = models.ManyToManyField("users.CustomUser", through="UserNotification", related_name="notifications")
    last_event_at = models.DateTimeField()
    identifier = models.CharField(max_length=40)
    event_data = SanitizedJSONField(default=dict, blank=True)
    links = SanitizedJSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "identifier"],
                condition=~models.Q(identifier=""),
                name="unique_notification_per_team_and_identifier",
            ),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.last_event_at is None:
            self.last_event_at = self.created_at
        super().save(*args, **kwargs)


class UserNotification(BaseTeamModel):
    # TODO: Remove model
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE)
    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE)
    read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = ("notification", "user")

    def __str__(self):
        return f"{self.user} - {self.notification.title}"


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


class NotificationEvent(BaseTeamModel):
    event_type = models.ForeignKey(EventType, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    message = models.TextField()
    links = SanitizedJSONField(null=True)


class EventUserQuerySet(models.QuerySet):
    def with_latest_event(self):
        latest_event = NotificationEvent.objects.filter(
            event_type=models.OuterRef("event_type"),
            team=models.OuterRef("team"),
        ).order_by("-created_at")
        return self.annotate(
            latest_event_title=models.Subquery(latest_event.values("title")[:1]),
            latest_event_message=models.Subquery(latest_event.values("message")[:1]),
            latest_event_links=models.Subquery(latest_event.values("links")[:1]),
            last_event_at=models.Subquery(latest_event.values("created_at")[:1]),
        )

    def with_mute_status(self) -> models.QuerySet:
        return self.annotate(
            is_muted=models.Case(
                models.When(muted_until__gt=timezone.now(), then=True),
                default=False,
                output_field=models.BooleanField(),
            )
        )


class EventUserManager(models.Manager.from_queryset(EventUserQuerySet)):
    pass


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
            args=[self.team.slug, self.event_type_id],
        )
