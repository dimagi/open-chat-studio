"""Data model for AI cost tracking."""

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext

from apps.teams.models import BaseTeamModel, Team
from apps.utils.fields import SanitizedJSONField


class ServiceKind(models.TextChoices):
    """Billing dimension. Each kind has its own PricingRule per (provider, model)."""

    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    LLM_CACHED_INPUT = "llm_cached_input"
    LLM_CACHE_WRITE = "llm_cache_write"


class Confidence(models.TextChoices):
    """Provenance of a UsageRecord's token count, not its pricing state."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class PricingSource(models.TextChoices):
    """Where a PricingRule's rate came from."""

    SEED = "seed"
    MANUAL = "manual"
    IMPORT = "import"


class PricingRule(models.Model):
    """A pricing rule. `team=NULL` means a global rule. Effectively
    write-once: rate changes close the active rule via `effective_to` and
    insert a new one. Not a `BaseTeamModel` subclass because `team` is
    nullable here and `VersioningMixin` doesn't apply.
    """

    team = models.ForeignKey(
        Team,
        verbose_name=gettext("Team"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    provider_type = models.CharField(max_length=64)
    model_name = models.CharField(max_length=128)
    service_kind = models.CharField(max_length=32, choices=ServiceKind.choices)
    unit_price = models.DecimalField(max_digits=14, decimal_places=8)
    currency = models.CharField(max_length=3, default="USD")
    effective_from = models.DateTimeField(default=timezone.now)
    effective_to = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=16, choices=PricingSource.choices, default=PricingSource.SEED)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey("users.CustomUser", null=True, blank=True, on_delete=models.DO_NOTHING)

    class Meta:
        indexes = [
            models.Index(fields=["team", "provider_type", "model_name", "service_kind", "effective_from"]),
        ]
        constraints = [
            # nulls_distinct=False so multiple `team=NULL` rows can't be active simultaneously.
            models.UniqueConstraint(
                fields=["team", "provider_type", "model_name", "service_kind"],
                condition=Q(effective_to__isnull=True),
                nulls_distinct=False,
                name="cost_tracking_unique_active_pricing_rule",
            ),
        ]

    def __str__(self):
        """Show team scope, full key, and current rate."""
        scope = self.team_id if self.team_id else "global"
        return f"[{scope}] {self.provider_type}/{self.model_name}/{self.service_kind} @ {self.unit_price}"


class UsageRecord(BaseTeamModel):
    """One row per (trace, model, service_kind) bucket. Snapshots
    `unit_price` / `currency` so historical rows are stable across rate changes.
    """

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    service_kind = models.CharField(max_length=32, choices=ServiceKind.choices)
    provider_type = models.CharField(max_length=64)
    model_name = models.CharField(max_length=128)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True)

    # Denormalised snapshot of the rule's rate at write time. Anchor "priced"
    # on `pricing_rule IS NOT NULL` (the primary source).
    unit_price = models.DecimalField(max_digits=14, decimal_places=8, null=True)
    cost = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    currency = models.CharField(max_length=3, default="USD")
    confidence = models.CharField(max_length=16, choices=Confidence.choices, default=Confidence.EXACT)

    experiment = models.ForeignKey("experiments.Experiment", null=True, on_delete=models.SET_NULL)
    session = models.ForeignKey("experiments.ExperimentSession", null=True, on_delete=models.SET_NULL)
    participant = models.ForeignKey("experiments.Participant", null=True, on_delete=models.SET_NULL)
    trace = models.ForeignKey("trace.Trace", null=True, on_delete=models.SET_NULL)
    # PROTECT so a rule with usage history can't be hard-deleted — keeps
    # `pricing_rule IS NOT NULL` as a stable historical "priced" anchor.
    pricing_rule = models.ForeignKey(PricingRule, null=True, on_delete=models.PROTECT)

    # Known keys: `estimator` (confidence=ESTIMATED), `missing_usage_calls` (confidence=UNKNOWN).
    extra = SanitizedJSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["team", "timestamp"]),
            models.Index(fields=["team", "experiment", "timestamp"]),
            models.Index(fields=["team", "session", "timestamp"]),
            models.Index(fields=["team", "model_name", "timestamp"]),
            models.Index(fields=["team", "confidence", "timestamp"]),
        ]
