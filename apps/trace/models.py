from __future__ import annotations

import uuid
from uuid import UUID

from django.db import models
from django_pydantic_field import SchemaField
from pydantic import BaseModel

from apps.annotations.models import TaggedModelMixin, UserCommentsMixin
from apps.teams.models import BaseTeamModel


class SpanError(BaseModel):
    # TODO: Move to data_structures file
    error_display: str
    raw_error: str
    sentry_trace_id: str


class TraceStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    PENDING = "pending", "Pending"
    ERROR = "error", "Error"


class Trace(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    trace_id = models.UUIDField(default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        "experiments.Experiment", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    session = models.ForeignKey(
        "experiments.ExperimentSession", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    participant = models.ForeignKey(
        "experiments.Participant", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    input_message = models.ForeignKey(
        "chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="input_message_trace"
    )
    output_message = models.ForeignKey(
        "chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="output_message_trace"
    )
    team = models.ForeignKey("teams.team", on_delete=models.SET_NULL, null=True, related_name="traces")
    duration = models.IntegerField()

    def __str__(self):
        return f"Trace {self.experiment} {self.session} {self.duration}ms"


class Span(BaseTeamModel, TaggedModelMixin, UserCommentsMixin):
    """
    Represents a segment or operation within a trace, allowing for detailed
    tracking of sub-operations with their own metrics and data.
    """

    span_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(Trace, on_delete=models.CASCADE, related_name="spans")
    parent_span = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="child_spans"
    )
    name = models.CharField(max_length=255)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=TraceStatus.choices, default=TraceStatus.PENDING)
    input = models.JSONField(default=dict, blank=True)
    output = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    error = SchemaField(schema=SpanError | None, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["trace_id"]),
            models.Index(fields=["parent_span_id"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.span_id}"
