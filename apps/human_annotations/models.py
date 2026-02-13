from django.conf import settings
from django.db import models
from django.urls import reverse
from pydantic import TypeAdapter

from apps.evaluations.field_definitions import FieldDefinition
from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team
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

    def get_absolute_url(self):
        return reverse("human_annotations:schema_edit", args=[get_slug_for_team(self.team_id), self.id])

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

    def get_absolute_url(self):
        return reverse("human_annotations:queue_detail", args=[get_slug_for_team(self.team_id), self.id])

    def get_progress(self):
        """Return progress stats: total items, completed, percent."""
        total = self.items.count()
        completed = self.items.filter(status=AnnotationItemStatus.COMPLETED).count()
        percent = round((completed / total) * 100) if total > 0 else 0
        return {"total": total, "completed": completed, "percent": percent}


class AnnotationItemType(models.TextChoices):
    SESSION = "session", "Session"
    MESSAGE = "message", "Message"
    EXTERNAL = "external", "External Data"


class AnnotationItem(BaseTeamModel):
    """A single item in an annotation queue to be reviewed."""

    queue = models.ForeignKey(AnnotationQueue, on_delete=models.CASCADE, related_name="items")
    item_type = models.CharField(max_length=20, choices=AnnotationItemType.choices)
    status = models.CharField(
        max_length=20,
        choices=AnnotationItemStatus.choices,
        default=AnnotationItemStatus.PENDING,
    )

    # Linked objects (nullable depending on item_type)
    session = models.ForeignKey(
        "experiments.ExperimentSession",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="annotation_items",
    )
    message = models.ForeignKey(
        "chat.ChatMessage",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="annotation_items",
    )

    # For external/CSV data
    external_data = SanitizedJSONField(default=dict, blank=True)

    # Denormalized review count for efficient querying
    review_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["queue", "status"]),
            models.Index(fields=["queue", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["queue", "session"],
                condition=models.Q(session__isnull=False),
                name="unique_session_per_queue",
            ),
            models.UniqueConstraint(
                fields=["queue", "message"],
                condition=models.Q(message__isnull=False),
                name="unique_message_per_queue",
            ),
        ]

    def __str__(self):
        if self.session_id:
            return f"Session {self.session.external_id}"
        if self.message_id:
            return f"Message {self.message_id}"
        return f"External item {self.id}"

    def update_status(self):
        """Update item status based on review count vs queue requirement."""
        if self.review_count >= self.queue.num_reviews_required:
            self.status = AnnotationItemStatus.COMPLETED
        elif self.review_count > 0:
            self.status = AnnotationItemStatus.IN_PROGRESS
        self.save(update_fields=["status"])


class AnnotationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"


class Annotation(BaseTeamModel):
    """A single review/annotation submitted by a reviewer for an item."""

    item = models.ForeignKey(AnnotationItem, on_delete=models.CASCADE, related_name="annotations")
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="annotations",
    )
    data = SanitizedJSONField(default=dict, help_text="Annotation data matching the queue's schema")
    status = models.CharField(
        max_length=20,
        choices=AnnotationStatus.choices,
        default=AnnotationStatus.SUBMITTED,
    )

    class Meta:
        unique_together = ("item", "reviewer")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Annotation by {self.reviewer} on item {self.item_id}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and self.status == AnnotationStatus.SUBMITTED:
            self._update_item_review_count()

    def _update_item_review_count(self):
        """Increment item review count and update status."""
        count = self.item.annotations.filter(status=AnnotationStatus.SUBMITTED).count()
        self.item.review_count = count
        self.item.save(update_fields=["review_count"])
        self.item.update_status()
