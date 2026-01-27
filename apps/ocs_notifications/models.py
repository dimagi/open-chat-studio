from django.db import models

from apps.utils.models import BaseModel


class CategoryChoices(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class Notification(BaseModel):
    title = models.CharField(max_length=255)
    message = models.TextField()
    category = models.CharField(max_length=20, choices=CategoryChoices.choices)
    users = models.ManyToManyField("users.CustomUser", through="UserNotification", related_name="notifications")

    def __str__(self):
        return self.title


class UserNotification(BaseModel):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE)
    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE)
    read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = ("notification", "user")

    def __str__(self):
        return f"{self.user} - {self.notification.title}"
