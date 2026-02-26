from __future__ import annotations

import uuid

from django.db import models
from django.urls import reverse

from apps.teams.utils import get_slug_for_team
from apps.utils.fields import SanitizedJSONField


class TraceStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    PENDING = "pending", "Pending"
    ERROR = "error", "Error"


class Trace(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    trace_id = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=32, choices=TraceStatus.choices, default=TraceStatus.PENDING, db_index=True)
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
    participant_data = SanitizedJSONField(
        default=dict, blank=True, help_text="Snapshot of participant data at the time of the trace"
    )
    session_state = SanitizedJSONField(
        default=dict, blank=True, help_text="Snapshot of session state at the time of the trace"
    )
    experiment_version_number = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True, help_text="Error message if the trace failed")

    def __str__(self):
        return f"Trace {self.experiment} {self.session} {self.duration}ms"

    def get_absolute_url(self):
        return reverse("trace:trace_detail", args=[get_slug_for_team(self.team_id), self.id])

    def duration_seconds(self) -> float:
        return round(self.duration / 1000, 2)
