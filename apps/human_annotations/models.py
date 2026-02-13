from django.conf import settings
from django.db import models
from pydantic import TypeAdapter

from apps.evaluations.field_definitions import FieldDefinition
from apps.teams.models import BaseTeamModel
from apps.utils.fields import SanitizedJSONField


class QueueStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    ARCHIVED = "archived", "Archived"


class AnnotationItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    FLAGGED = "flagged", "Flagged"


class AnnotationSchema(BaseTeamModel):
    """Defines the fields annotators will fill out. Reuses FieldDefinition from evaluations."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    schema = SanitizedJSONField(
        default=dict,
        help_text="Dict of field_name -> FieldDefinition JSON (same format as evaluator output_schema)",
    )

    class Meta:
        unique_together = ("team", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_field_definitions(self) -> dict[str, FieldDefinition]:
        """Parse the raw JSON schema into typed FieldDefinition objects."""
        adapter = TypeAdapter(FieldDefinition)
        return {name: adapter.validate_python(defn) for name, defn in self.schema.items()}


class AnnotationQueue(BaseTeamModel):
    """A queue of items to be annotated by assigned reviewers."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    schema = models.ForeignKey(AnnotationSchema, on_delete=models.PROTECT, related_name="queues")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_annotation_queues",
    )
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="assigned_annotation_queues")
    num_reviews_required = models.PositiveSmallIntegerField(
        default=1,
        help_text="Number of reviews required before an item is marked complete (1-10)",
    )
    status = models.CharField(max_length=20, choices=QueueStatus.choices, default=QueueStatus.ACTIVE)

    class Meta:
        unique_together = ("team", "name")
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_progress(self):
        """Return progress stats: total items, completed, percent."""
        total = self.items.count()
        completed = self.items.filter(status=AnnotationItemStatus.COMPLETED).count()
        percent = round((completed / total) * 100) if total > 0 else 0
        return {"total": total, "completed": completed, "percent": percent}
