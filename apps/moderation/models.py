from django.conf import settings
from django.db import models
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.moderation import model_audit_fields
from apps.teams.models import BaseTeamModel


class DeniedParticipantSource(models.TextChoices):
    AUTO = "auto", "Automatic"
    MANUAL = "manual", "Manual"
    CODE_NODE = "code_node", "Code node"


@audit_fields(*model_audit_fields.DENIED_PARTICIPANT_FIELDS, audit_special_queryset_writes=True)
class DeniedParticipant(BaseTeamModel):
    """A participant on the team's denylist, one row per team and participant."""

    objects = AuditingManager()
    participant = models.ForeignKey("experiments.Participant", on_delete=models.CASCADE, related_name="denials")
    source = models.CharField(max_length=16, choices=DeniedParticipantSource.choices)
    reason = models.TextField(blank=True)
    experiment = models.ForeignKey(
        "experiments.Experiment", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    chat_message = models.ForeignKey(
        "chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "participant"], name="unique_denied_participant_per_team"),
        ]

    def __str__(self):
        return f"{self.participant} ({self.get_source_display()})"
