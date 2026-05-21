from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from apps.teams.models import BaseTeamModel


class Score(BaseTeamModel):
    """A single typed score attached to a measurement-unit target.

    Written by both automated evaluators (via `automated_result` FK) and human
    reviewers (via `review` FK). See docs/design/unified-assessment.md for the
    long-term design; this is the lean v1 shape for basic concordance.
    """

    class Source(models.TextChoices):
        LLM_JUDGE = "llm_judge", "LLM judge"
        PROGRAMMATIC = "programmatic", "Programmatic"
        HUMAN_REVIEW = "human_review", "Human review"
        # below here are placeholders for future use
        USER_FEEDBACK = "user_feedback", "User feedback"
        SYSTEM = "system", "System"

    class DataType(models.TextChoices):
        NUMERIC = "numeric", "Numeric"
        CATEGORICAL = "categorical", "Categorical"
        BOOLEAN = "boolean", "Boolean"

    target_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    target_object_id = models.PositiveIntegerField()
    target = GenericForeignKey("target_content_type", "target_object_id")

    name = models.CharField(max_length=255)
    data_type = models.CharField(max_length=20, choices=DataType.choices)
    value_numeric = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    value_string = models.TextField(null=True, blank=True)  # noqa: DJ001

    source = models.CharField(max_length=20, choices=Source.choices)

    automated_result = models.ForeignKey(
        "evaluations.EvaluationResult",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scores",
    )
    review = models.ForeignKey(
        "human_annotations.Annotation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scores",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["automated_result", "name"],
                condition=Q(automated_result__isnull=False),
                name="score_unique_per_automated_result_field",
            ),
            models.UniqueConstraint(
                fields=["review", "name"],
                condition=Q(review__isnull=False),
                name="score_unique_per_review_field",
            ),
            models.CheckConstraint(
                condition=Q(value_numeric__isnull=False) | Q(value_string__isnull=False),
                name="score_value_present",
            ),
        ]
        indexes = [
            models.Index(fields=["target_content_type", "target_object_id", "name", "source"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        value = self.value_numeric if self.value_numeric is not None else self.value_string
        return f"Score({self.name}={value}, source={self.source})"
