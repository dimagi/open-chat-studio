import logging

from django.conf import settings
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Sum
from django.urls import reverse
from django.utils import timezone
from pydantic import TypeAdapter

from apps.assessments.score_writers import write_scores_from_annotation
from apps.evaluations.field_definitions import FieldDefinition
from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team
from apps.utils.fields import SanitizedJSONField

logger = logging.getLogger("ocs.human_annotations")


class QueueStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    ARCHIVED = "archived", "Archived"


class AnnotationItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    AWAITING_RESOLUTION = "awaiting_resolution", "Awaiting resolution"
    COMPLETED = "completed", "Completed"
    FLAGGED = "flagged", "Flagged"


def _compute_item_status(review_count: int, required: int, has_authoritative: bool) -> str:
    """Pure status rule shared by per-item updates and the bulk recompute.

    An item is COMPLETED once it has an authoritative annotation, or when it only has a
    single review (one reviewer can't disagree with anyone, so there is nothing to
    resolve). With two or more reviews and no authoritative pick it is AWAITING_RESOLUTION
    — including after ``num_reviews_required`` is lowered, since lowering the requirement
    does not retroactively resolve a disagreement between existing reviews.
    """
    if review_count == 0:
        return AnnotationItemStatus.PENDING
    if review_count < required:
        return AnnotationItemStatus.IN_PROGRESS
    if has_authoritative or review_count == 1:
        return AnnotationItemStatus.COMPLETED
    return AnnotationItemStatus.AWAITING_RESOLUTION


class AnnotationQueueQuerySet(models.QuerySet):
    def visible_to(self, user, team):
        """Return queues visible to the user within a team.

        Admins (with add_annotationqueue permission) see all team queues.
        Reviewers only see queues they are directly assigned to.
        """
        qs = self.filter(team=team)
        if not user.has_perm("human_annotations.add_annotationqueue"):
            qs = qs.filter(assignees=user)
        return qs


class AnnotationQueueManager(models.Manager):
    def get_queryset(self):
        return AnnotationQueueQuerySet(self.model, using=self._db)

    def visible_to(self, user, team):
        return self.get_queryset().visible_to(user, team)


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

    objects = AnnotationQueueManager()

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
        awaiting_resolution_items = self.items.filter(status=AnnotationItemStatus.AWAITING_RESOLUTION).count()
        flagged_items = self.items.filter(status=AnnotationItemStatus.FLAGGED).count()

        total_reviews_needed = total_items * self.num_reviews_required
        reviews_done = self.items.aggregate(total=Sum("review_count"))["total"] or 0
        review_percent = round((reviews_done / total_reviews_needed) * 100) if total_reviews_needed > 0 else 0

        return {
            "total_items": total_items,
            "completed_items": completed_items,
            "awaiting_resolution_items": awaiting_resolution_items,
            "flagged_items": flagged_items,
            "total_reviews_needed": total_reviews_needed,
            "reviews_done": reviews_done,
            "percent": review_percent,
        }

    def resync_items(self):
        """Re-sync items to the current ``num_reviews_required``.

        Recomputes every (non-FLAGGED) item's status and, when the queue becomes
        multi-review, clears auto-assigned authoritative flags (left over from
        single-review submissions, identified by a null ``authoritative_set_by``)
        so those items go through normal resolution; human-picked authoritative
        flags are kept. If any flags were cleared, stored aggregates are
        recomputed too, since clearing them changes aggregation. FLAGGED items are
        left untouched, matching ``AnnotationItem.update_status``.
        """
        with transaction.atomic():
            cleared = 0
            if self.num_reviews_required > 1:
                cleared = Annotation.objects.filter(
                    item__queue=self,
                    is_authoritative=True,
                    authoritative_set_by__isnull=True,
                ).update(is_authoritative=False, authoritative_set_at=None)
            elif self.num_reviews_required == 1:
                # Mirror submission's auto-mark: a lone review on a single-review queue is the
                # authoritative answer. Without this, lowering the requirement to 1 completes the
                # item via the single-review rule but leaves it with no authoritative annotation.
                # set_by stays null (auto-assigned) so a later raise above 1 clears it again.
                Annotation.objects.filter(
                    item__queue=self,
                    item__review_count=1,
                    status=AnnotationStatus.SUBMITTED,
                    is_authoritative=False,
                ).exclude(item__status=AnnotationItemStatus.FLAGGED).update(
                    is_authoritative=True, authoritative_set_at=timezone.now()
                )

            items = (
                self.items.exclude(status=AnnotationItemStatus.FLAGGED)
                .select_for_update()
                .annotate(
                    has_authoritative=Exists(
                        Annotation.objects.filter(
                            item=OuterRef("pk"),
                            status=AnnotationStatus.SUBMITTED,
                            is_authoritative=True,
                        )
                    )
                )
            )
            to_update = []
            for item in items:
                new_status = _compute_item_status(item.review_count, self.num_reviews_required, item.has_authoritative)
                if new_status != item.status:
                    item.status = new_status
                    to_update.append(item)
            if to_update:
                AnnotationItem.objects.bulk_update(to_update, ["status"])

        # Clearing authoritative flags changes aggregation (authoritative vs all-submitted),
        # so refresh stored aggregates. Run outside the transaction, like recompute_queue_aggregates.
        if cleared:
            from apps.human_annotations.aggregation import (  # noqa: PLC0415 - circular: aggregation imports human_annotations.models
                compute_aggregates_for_queue,
            )

            try:
                compute_aggregates_for_queue(self)
            except Exception:
                logger.exception("Failed to recompute aggregates for queue %s during resync", self.id)


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

    def _has_authoritative_annotation(self) -> bool:
        return self.annotations.filter(status=AnnotationStatus.SUBMITTED, is_authoritative=True).exists()

    def update_status(self, save=True):
        """Update item status based on review count, authoritative flag, and queue requirement.
        Preserves FLAGGED status — only explicit unflagging clears it."""
        if self.status == AnnotationItemStatus.FLAGGED:
            return

        self.status = _compute_item_status(
            self.review_count,
            self.queue.num_reviews_required,
            self._has_authoritative_annotation(),
        )

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
    is_authoritative = models.BooleanField(default=False)
    authoritative_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authoritative_annotations_set",
    )
    authoritative_set_at = models.DateTimeField(null=True, blank=True)
    data = SanitizedJSONField(default=dict, help_text="Annotation data matching the queue's schema")
    status = models.CharField(
        max_length=20,
        choices=AnnotationStatus.choices,
        default=AnnotationStatus.SUBMITTED,
    )

    class Meta:
        unique_together = ("item", "reviewer")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["item"],
                condition=models.Q(is_authoritative=True),
                name="one_authoritative_annotation_per_item",
            ),
        ]

    def __str__(self):
        return f"Annotation by {self.reviewer} on item {self.item_id}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        submitted = self.status == AnnotationStatus.SUBMITTED

        if is_new and submitted:
            # Lock the item across the auto-mark check + save so two concurrent submissions
            # on a single-review queue can't both pass the check and violate
            # one_authoritative_annotation_per_item.
            with transaction.atomic():
                item = AnnotationItem.objects.select_for_update().get(pk=self.item_id)
                self._maybe_auto_mark_authoritative()
                super().save(*args, **kwargs)
                item.review_count = item.annotations.filter(status=AnnotationStatus.SUBMITTED).count()
                item.update_status(save=False)
                item.save(update_fields=["review_count", "status"])
            self.recompute_queue_aggregates(item.queue)
        else:
            super().save(*args, **kwargs)

        # Re-run score writes on every save of a SUBMITTED annotation (including edits)
        # so concordance never reads stale data. Writer is idempotent. Runs outside the
        # transaction so a writer failure cannot roll back the annotation save.
        if submitted:
            try:
                write_scores_from_annotation(self)
            except Exception:
                logger.exception("Failed to write Score rows for annotation %s", self.id)

    def _maybe_auto_mark_authoritative(self):
        """For single-reviewer queues, auto-mark the first submission as authoritative.
        Skips when another authoritative annotation already exists on the item (handles
        over-budget submissions and avoids violating the partial unique constraint).
        Caller must hold a row-level lock on the item to serialize concurrent submissions."""
        queue = self.item.queue
        if queue.num_reviews_required != 1:
            return
        if Annotation.objects.filter(item=self.item, is_authoritative=True).exists():
            return
        self.is_authoritative = True
        self.authoritative_set_by = None
        self.authoritative_set_at = timezone.now()

    def recompute_queue_aggregates(self, queue=None):
        """Recompute aggregates for the queue this annotation belongs to."""
        if queue is None:
            queue = self.item.queue
        from apps.human_annotations.aggregation import (  # noqa: PLC0415 - circular: aggregation imports human_annotations.models
            compute_aggregates_for_queue,
        )

        try:
            compute_aggregates_for_queue(queue)
        except Exception:
            logger.exception("Failed to recompute aggregates for queue %s", queue.id)


class AnnotationQueueAggregate(BaseTeamModel):
    """Stores aggregated annotation results for a queue."""

    queue = models.OneToOneField(AnnotationQueue, on_delete=models.CASCADE, related_name="aggregate")
    aggregates = models.JSONField(default=dict, help_text="Aggregated stats per schema field")
