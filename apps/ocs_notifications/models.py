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

    def __str__(self):
        return self.title


class NotificationReceipt(BaseModel):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE)
    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE, related_name="notifications", db_index=True)
    read = models.BooleanField(default=False, db_index=True)

    class Meta:
        unique_together = ("notification", "user")

    def __str__(self):
        return f"{self.user} - {self.notification.title}"
