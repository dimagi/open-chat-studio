from __future__ import annotations

import uuid

from django.db import models
from django.urls import reverse

from apps.teams.models import BaseTeamModel


class TraceStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    PENDING = "pending", "Pending"
    ERROR = "error", "Error"


class Trace(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    trace_id = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=32, choices=TraceStatus.choices, default=TraceStatus.PENDING)
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

    def get_absolute_url(self):
        return reverse("trace:trace_detail", args=[self.team.slug, self.id])

    def duration_seconds(self) -> float:
        return round(self.duration / 1000, 2)

    def span(
        self,
        span_id: uuid.UUID,
        span_name: str,
        inputs: dict[str, any],
        metadata: dict[str, any] | None = None,
    ) -> Span:
        return _create_span(
            trace_id=self.id,
            parent_span=None,
            team_id=self.team_id,
            span_id=span_id,
            span_name=span_name,
            inputs=inputs,
            metadata=metadata,
        )


class Span(BaseTeamModel):
    """
    Represents a segment or operation within a trace, allowing for detailed
    tracking of sub-operations with their own metrics and data.
    """

    span_id = models.UUIDField(default=uuid.uuid4, editable=False)
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
    error = models.CharField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["trace_id"]),
            models.Index(fields=["parent_span_id"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.span_id}"

    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return round((self.end_time - self.start_time).total_seconds(), 2)
        return 0

    def span(
        self,
        span_id: uuid.UUID,
        span_name: str,
        inputs: dict[str, any],
        metadata: dict[str, any] | None = None,
    ) -> Span:
        return _create_span(
            trace_id=self.trace_id,
            parent_span=self,
            team_id=self.team_id,
            span_id=span_id,
            span_name=span_name,
            inputs=inputs,
            metadata=metadata,
        )


def _create_span(
    trace_id: int,
    parent_span: Span,
    team_id: int,
    span_id: uuid.UUID,
    span_name: str,
    inputs: dict[str, any],
    metadata: dict[str, any] | None = None,
) -> Span:
    return Span.objects.create(
        trace_id=trace_id,
        parent_span=parent_span,
        span_id=span_id,
        name=span_name,
        team_id=team_id,
        input=inputs,
        metadata=metadata or {},
    )
