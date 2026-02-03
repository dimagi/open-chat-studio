from django.db import models

from apps.teams.models import BaseTeamModel


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
    identifier = models.CharField(blank=True)

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

    class Meta:
        verbose_name_plural = "User Notification Preferences"

    def __str__(self):
        return f"Notification preferences for {self.user}"
