"""Read path for cost tracking. The dashboard, REST endpoints, and weekly
digest all consume this. Aggregations are single-query, team-scoped, and
hit the `(team, timestamp)` / `(team, experiment, timestamp)` indexes.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce

from apps.cost_tracking.models import Confidence, PricingRule, UsageRecord
from apps.teams.models import Team

logger = logging.getLogger("ocs.cost_tracking")

_ZERO = Decimal(0)
_COST_FIELD = DecimalField(max_digits=14, decimal_places=8)
_QUANTITY_FIELD = DecimalField(max_digits=18, decimal_places=4)


@dataclass(frozen=True)
class CostSummary:
    """Period-over-period rollup for the dashboard panel."""

    period_start: datetime
    period_end: datetime
    total_cost: Decimal
    previous_period_cost: Decimal
    delta_pct: float | None
    exact_cost: Decimal
    estimated_cost: Decimal
    unknown_call_count: int
    last_synced: datetime | None


@dataclass(frozen=True)
class BotSpend:
    """Per-experiment spend row for the by-bot table."""

    experiment_id: int
    experiment_name: str
    cost: Decimal
    tokens: int
    sessions: int
    cost_per_session: Decimal | None


def cost_summary(team: Team, *, start: datetime, end: datetime) -> CostSummary:
    """Total cost in [start, end), delta vs the equal-length prior period,
    and a confidence breakdown so the dashboard footer can show what share
    of the spend is estimated.
    """
    previous_start = start - (end - start)
    period_q = Q(timestamp__gte=start, timestamp__lt=end)
    previous_q = Q(timestamp__gte=previous_start, timestamp__lt=start)

    agg = UsageRecord.objects.filter(team=team).aggregate(
        total=Coalesce(Sum("cost", filter=period_q), _ZERO, output_field=_COST_FIELD),
        previous=Coalesce(Sum("cost", filter=previous_q), _ZERO, output_field=_COST_FIELD),
        exact=Coalesce(
            Sum("cost", filter=period_q & Q(confidence=Confidence.EXACT)),
            _ZERO,
            output_field=_COST_FIELD,
        ),
        estimated=Coalesce(
            Sum("cost", filter=period_q & Q(confidence=Confidence.ESTIMATED)),
            _ZERO,
            output_field=_COST_FIELD,
        ),
        unknown_rows=Count("id", filter=period_q & Q(confidence=Confidence.UNKNOWN)),
    )

    return CostSummary(
        period_start=start,
        period_end=end,
        total_cost=agg["total"],
        previous_period_cost=agg["previous"],
        delta_pct=_safe_pct(agg["total"] - agg["previous"], agg["previous"]),
        exact_cost=agg["exact"],
        estimated_cost=agg["estimated"],
        unknown_call_count=agg["unknown_rows"],
        last_synced=last_synced_at(),
    )


def top_n_bots(team: Team, *, start: datetime, end: datetime, limit: int = 10) -> list[BotSpend]:
    """Top experiments by cost in the period. Records with a null experiment
    (e.g. trace whose experiment was hard-deleted) are excluded.
    """
    rows = (
        UsageRecord.objects.filter(
            team=team,
            timestamp__gte=start,
            timestamp__lt=end,
            experiment__isnull=False,
        )
        .values("experiment_id", "experiment__name")
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            tokens=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
            sessions=Count("session_id", distinct=True),
        )
        .order_by("-cost")[:limit]
    )
    return [_bot_spend_from_row(row) for row in rows]


def last_synced_at() -> datetime | None:
    """Most recent `effective_from` on a globally-scoped PricingRule.
    The dashboard footer renders "Pricing last synced YYYY-MM-DD".
    """
    return (
        PricingRule.objects.filter(team__isnull=True)
        .order_by("-effective_from")
        .values_list("effective_from", flat=True)
        .first()
    )


def _bot_spend_from_row(row: dict) -> BotSpend:
    sessions = row["sessions"]
    cost = row["cost"]
    return BotSpend(
        experiment_id=row["experiment_id"],
        experiment_name=row["experiment__name"],
        cost=cost,
        tokens=int(row["tokens"] or 0),
        sessions=sessions,
        cost_per_session=(cost / sessions) if sessions else None,
    )


def _safe_pct(delta: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    return float(delta / previous * 100)
