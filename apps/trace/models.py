from __future__ import annotations

import uuid

import dictdiffer
from django.db import models
from django.db.models import Q
from django.urls import reverse

from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team
from apps.utils.fields import SanitizedJSONField, sanitize_json_data


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
    participant_data_diff = SanitizedJSONField(
        default=list, null=True, blank=True, help_text="Diff of participant data changes during this trace"
    )
    session_state = SanitizedJSONField(
        default=dict, blank=True, help_text="Snapshot of session state at the time of the trace"
    )
    experiment_version_number = models.PositiveIntegerField(null=True, blank=True)
    trace_metadata = SanitizedJSONField(
        default=dict, blank=True, help_text="Metadata from trace providers (e.g. Langfuse trace_id, trace_url)"
    )
    error = models.TextField(blank=True, help_text="Error message if the trace failed")
    n_turns = models.IntegerField(null=True, blank=True, help_text="Number of LLM calls during pipeline execution")
    n_toolcalls = models.IntegerField(null=True, blank=True, help_text="Number of tool invocations across all turns")

    class Meta:
        indexes = [
            models.Index(
                fields=["team", "-timestamp"],
                name="trace_team_timestamp_idx",
                condition=~Q(status=TraceStatus.PENDING),
            ),
            models.Index(
                fields=["experiment", "-timestamp"],
                name="trace_experiment_timestamp_idx",
            ),
            models.Index(
                fields=["session", "-timestamp"],
                name="trace_session_timestamp_idx",
            ),
            # Supports the global (cross-team) date-range scan in the admin usage export.
            models.Index(fields=["timestamp"], name="trace_timestamp_idx"),
        ]

    def __str__(self):
        return f"Trace {self.experiment} {self.session} {self.duration}ms"

    def get_absolute_url(self):
        return reverse("trace:trace_detail", args=[get_slug_for_team(self.team_id), self.id])

    def duration_seconds(self) -> float:
        return round(self.duration / 1000, 2)


class CustomActionRequest(BaseTeamModel):
    """One HTTP request a custom action tool sent during a trace, with credentials already redacted."""

    trace = models.ForeignKey(Trace, on_delete=models.CASCADE, related_name="custom_action_requests")
    custom_action = models.ForeignKey(
        "custom_actions.CustomAction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logged_requests",
    )
    operation = models.CharField(max_length=255)
    method = models.CharField(max_length=16)
    url = models.TextField()
    request_headers = SanitizedJSONField(default=dict, blank=True)
    request_body = models.TextField(blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Empty when no response came back")
    response_headers = SanitizedJSONField(default=dict, blank=True)
    response_body = models.TextField(blank=True)
    duration_ms = models.PositiveIntegerField()
    error = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["custom_action", "-created_at"], name="ca_request_action_created_idx")]

    @classmethod
    def from_request_log(cls, request_log: dict, trace: Trace) -> CustomActionRequest:
        """Build an unsaved row from the record ``apps.chat.agent.request_log`` dispatches."""
        log = sanitize_json_data(request_log)
        return cls(
            team_id=trace.team_id,
            trace=trace,
            custom_action_id=log["custom_action_id"],
            operation=log["tool"],
            method=log["method"],
            url=log["url"],
            request_headers=log["request_headers"],
            request_body=log["request_body"] or "",
            status_code=log["status_code"],
            response_headers=log["response_headers"],
            response_body=log["response_body"] or "",
            duration_ms=log["duration_ms"],
            error=log["error"],
        )


def participant_data_from_trace(trace: Trace) -> dict:
    """Reconstruct the participant data state as it existed after the given trace.

    Applies ``trace.participant_data_diff`` (if present) to ``trace.participant_data``
    to produce the final state. Returns the snapshot as-is when no diff is recorded.
    """
    snapshot = trace.participant_data or {}
    if trace.participant_data_diff:
        return dictdiffer.patch(trace.participant_data_diff, snapshot)
    return snapshot
