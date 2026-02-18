from django.conf import settings
from django.db import models, transaction
from django.db.models import Sum
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


class AnnotationQueue(BaseTeamModel):
    """A queue of items to be annotated by assigned reviewers."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    schema = SanitizedJSONField(
        default=dict,
        help_text="Dict of field_name -> FieldDefinition JSON (same format as evaluator output_schema)",
    )
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(num_reviews_required__gte=1, num_reviews_required__lte=10),
                name="num_reviews_required_range",
            ),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("human_annotations:queue_detail", args=[get_slug_for_team(self.team_id), self.id])

    def get_field_definitions(self) -> dict[str, FieldDefinition]:
        """Parse the raw JSON schema into typed FieldDefinition objects."""
        adapter = TypeAdapter(FieldDefinition)
        return {name: adapter.validate_python(defn) for name, defn in self.schema.items()}

    def get_progress(self):
        """Return progress stats including review-level progress for multi-review queues."""
        total_items = self.items.count()
        completed_items = self.items.filter(status=AnnotationItemStatus.COMPLETED).count()
        flagged_items = self.items.filter(status=AnnotationItemStatus.FLAGGED).count()

        total_reviews_needed = total_items * self.num_reviews_required
        reviews_done = self.items.aggregate(total=Sum("review_count"))["total"] or 0
        review_percent = round((reviews_done / total_reviews_needed) * 100) if total_reviews_needed > 0 else 0

        return {
            "total_items": total_items,
            "completed_items": completed_items,
            "flagged_items": flagged_items,
            "total_reviews_needed": total_reviews_needed,
            "reviews_done": reviews_done,
            "percent": review_percent,
        }


class AnnotationItemType(models.TextChoices):
    SESSION = "session", "Session"
    MESSAGE = "message", "Message"


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

    # Denormalized review count for efficient querying
    review_count = models.PositiveSmallIntegerField(default=0)

    # Append-only list of flags: [{"user": "<name>", "user_id": <id>, "reason": "...", "timestamp": "..."}]
    flags = SanitizedJSONField(default=list, blank=True)

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
            if self.__class__.session.is_cached(self):
                return f"Session {self.session.external_id}"
            return f"Session #{self.session_id}"
        if self.message_id:
            return f"Message {self.message_id}"
        return f"Item {self.id}"

    def update_status(self, save=True):
        """Update item status based on review count vs queue requirement.
        Preserves FLAGGED status — only explicit unflagging clears it."""
        if self.status == AnnotationItemStatus.FLAGGED:
            return
        if self.review_count >= self.queue.num_reviews_required:
            self.status = AnnotationItemStatus.COMPLETED
        elif self.review_count > 0:
            self.status = AnnotationItemStatus.IN_PROGRESS
        else:
            self.status = AnnotationItemStatus.PENDING
        if save:
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
        """Increment item review count, update status, and recompute queue aggregates."""
        with transaction.atomic():
            item = AnnotationItem.objects.select_for_update().get(pk=self.item_id)
            item.review_count = item.annotations.filter(status=AnnotationStatus.SUBMITTED).count()
            item.update_status(save=False)
            item.save(update_fields=["review_count", "status"])

        from apps.human_annotations.aggregation import compute_aggregates_for_queue

        compute_aggregates_for_queue(item.queue)


class AnnotationQueueAggregate(BaseTeamModel):
    """Stores aggregated annotation results for a queue."""

    queue = models.OneToOneField(AnnotationQueue, on_delete=models.CASCADE, related_name="aggregate")
    aggregates = models.JSONField(default=dict, help_text="Aggregated stats per schema field")
