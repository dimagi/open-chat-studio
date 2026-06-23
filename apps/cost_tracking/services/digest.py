"""Weekly operator digest. Surfaces two coverage gaps for cost tracking:
unpriced models (recorded with `unit_price IS NULL`) and unknown calls
(provider returned no usage_metadata at all). Aggregated cross-team so
the platform team can decide whether to backfill pricing or chase up a
provider integration.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, IntegerField, Sum
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce

from apps.cost_tracking.models import Confidence, UsageRecord

logger = logging.getLogger("ocs.cost_tracking")


@dataclass(frozen=True)
class UnpricedRow:
    provider_type: str
    model_name: str
    service_kind: str
    calls: int


@dataclass(frozen=True)
class UnknownRow:
    provider_type: str
    model_name: str
    service_kind: str
    missing_usage_calls: int


@dataclass(frozen=True)
class DigestSummary:
    period_start: datetime
    period_end: datetime
    unpriced_rows: list[UnpricedRow]
    unknown_rows: list[UnknownRow]

    @property
    def distinct_unpriced_models(self) -> int:
        return len({(r.provider_type, r.model_name) for r in self.unpriced_rows})

    @property
    def total_unknown_calls(self) -> int:
        return sum(r.missing_usage_calls for r in self.unknown_rows)

    @property
    def is_empty(self) -> bool:
        return not self.unpriced_rows and not self.unknown_rows


def build_digest(start: datetime, end: datetime) -> DigestSummary:
    """Two single-query aggregations, cross-team. `extra->>missing_usage_calls`
    is summed via JSON extraction so the digest reports actual call counts
    rather than UsageRecord row counts (rows fold multiple failed calls).
    """
    unpriced = list(
        UsageRecord.objects.filter(
            timestamp__gte=start,
            timestamp__lt=end,
            unit_price__isnull=True,
        )
        .values("provider_type", "model_name", "service_kind")
        .annotate(calls=Count("id"))
        .order_by("provider_type", "model_name", "service_kind")
    )
    unknown = list(
        UsageRecord.objects.filter(
            timestamp__gte=start,
            timestamp__lt=end,
            confidence=Confidence.UNKNOWN,
        )
        .values("provider_type", "model_name", "service_kind")
        .annotate(
            missing_usage_calls=Coalesce(
                Sum(Cast(KeyTextTransform("missing_usage_calls", "extra"), IntegerField())),
                0,
            )
        )
        .order_by("provider_type", "model_name", "service_kind")
    )
    return DigestSummary(
        period_start=start,
        period_end=end,
        unpriced_rows=[UnpricedRow(**r) for r in unpriced],
        unknown_rows=[UnknownRow(**r) for r in unknown],
    )
