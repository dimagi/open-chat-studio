from django.db import models

from apps.teams.models import BaseTeamModel
from apps.utils.fields import SanitizedJSONField


class LevelChoices(models.IntegerChoices):
    INFO = 0, "Info"
    WARNING = 1, "Warning"
    ERROR = 2, "Error"


class Notification(BaseTeamModel):
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
        unique_together = ("user", "team")

    def __str__(self):
        return f"Notification preferences for {self.user}"


class NotificationMute(BaseTeamModel):
    """Store user mute settings for notifications"""

    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE, related_name="notification_mutes")
    notification_identifier = models.CharField(max_length=255, help_text="Notification identifier to mute")
    muted_until = models.DateTimeField(null=True, help_text="When the mute expires. Null means permanent mute.")

    class Meta:
        verbose_name_plural = "Notification Mutes"
        unique_together = ("user", "team", "notification_identifier")
        indexes = [
            models.Index(fields=["user", "team", "muted_until"]),
        ]

    def __str__(self):
        text = f"{self.user} muted {self.notification_identifier}"
        if self.muted_until:
            text = f"{text} forever"
        else:
            text = f"{text} until {self.muted_until}"
        return text
