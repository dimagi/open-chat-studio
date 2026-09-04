from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession
from apps.utils.models import BaseModel


class EventLogStatusChoices(models.TextChoices):
    SUCCESS = "success"
    FAILURE = "failure"


class EventLog(BaseModel):
    # StaticTrigger, TimeoutTrigger, or ScheduledTrigger
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    session = models.ForeignKey(
        ExperimentSession, on_delete=models.CASCADE, related_name="event_logs", null=True, blank=True
    )
    chat_message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, related_name="event_logs", null=True, blank=True
    )
    status = models.CharField(choices=EventLogStatusChoices.choices)
    log = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]
