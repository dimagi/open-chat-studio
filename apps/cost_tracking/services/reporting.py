"""Read path for cost tracking. The dashboard, REST endpoints, and weekly
digest all consume this. Aggregations are single-query, team-scoped, and
hit the `(team, timestamp)` / `(team, experiment, timestamp)` indexes.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek

from apps.cost_tracking.models import Confidence, UsageRecord
from apps.experiments.models import ExperimentSession
from apps.teams.models import Team

logger = logging.getLogger("ocs.cost_tracking")

_ZERO = Decimal(0)
_COST_FIELD = DecimalField(max_digits=14, decimal_places=8)
_QUANTITY_FIELD = DecimalField(max_digits=18, decimal_places=4)

_GRANULARITY_TRUNC = {
    "daily": TruncDate,
    "weekly": TruncWeek,
    "monthly": TruncMonth,
}


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
    unpriced_call_count: int


@dataclass(frozen=True)
class ModelSpend:
    """Per-model spend row for a single session's usage breakdown."""

    model_name: str
    cost: Decimal
    tokens: int


@dataclass(frozen=True)
class SessionUsage:
    """Whole-session usage: total cost plus a per-model breakdown."""

    total_cost: Decimal
    by_model: list[ModelSpend]


@dataclass(frozen=True)
class ModelCoverageGap:
    """One (provider, model) with calls the dashboard couldn't fully account
    for - either unpriced (no matching rule) or missing usage data.
    """

    provider_type: str
    model_name: str
    call_count: int


@dataclass(frozen=True)
class CoverageGaps:
    """The models behind the panel's `unpriced_call_count` /
    `unknown_call_count`, so the warnings can name what's responsible.
    """

    unpriced: list[ModelCoverageGap]
    unknown: list[ModelCoverageGap]


def _scoped_records(
    team: Team,
    *,
    experiment_ids: list[int] | None = None,
    platform_names: list[str] | None = None,
    participant_ids: list[int] | None = None,
):
    """Team-scoped UsageRecords with the dashboard's chatbot / participant /
    platform filters applied (mirrors the cost panel's other charts). Platform
    is matched via the record's session, so records with no session are excluded
    when a platform filter is set. Tags are intentionally not supported here -
    usage records aren't tagged directly.
    """
    qs = UsageRecord.objects.filter(team=team)
    if experiment_ids:
        qs = qs.filter(experiment_id__in=experiment_ids)
    if participant_ids:
        qs = qs.filter(participant_id__in=participant_ids)
    if platform_names:
        qs = qs.filter(session__platform__in=platform_names)
    return qs


def cost_summary(
    team: Team,
    *,
    start: datetime,
    end: datetime,
    experiment_ids: list[int] | None = None,
    platform_names: list[str] | None = None,
    participant_ids: list[int] | None = None,
) -> CostSummary:
    """Total cost in [start, end), delta vs the equal-length prior period,
    and a confidence breakdown so the dashboard footer can show what share
    of the spend is estimated.
    """
    previous_start = start - (end - start)
    period_q = Q(timestamp__gte=start, timestamp__lt=end)
    previous_q = Q(timestamp__gte=previous_start, timestamp__lt=start)

    agg = _scoped_records(
        team, experiment_ids=experiment_ids, platform_names=platform_names, participant_ids=participant_ids
    ).aggregate(
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
        # Rows that got recorded but the resolver couldn't price (no matching
        # PricingRule). Excludes UNKNOWN-confidence rows because those have
        # their own counter. Distinct row counter, not a sum - these rows
        # contribute $0 to total_cost.
        unpriced_rows=Count("id", filter=period_q & Q(pricing_rule__isnull=True) & ~Q(confidence=Confidence.UNKNOWN)),
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
        unpriced_call_count=agg["unpriced_rows"],
    )


def costs_by_experiment(
    team: Team,
    *,
    start: datetime,
    end: datetime,
    experiment_ids: list[int] | None = None,
    platform_names: list[str] | None = None,
    participant_ids: list[int] | None = None,
) -> dict[int, Decimal]:
    """Total cost per experiment in the period, keyed by `experiment_id`.
    Feeds the dashboard's Bot Performance table cost column. Records with a
    null experiment (e.g. trace whose experiment was hard-deleted) are excluded.
    """
    rows = (
        _scoped_records(
            team, experiment_ids=experiment_ids, platform_names=platform_names, participant_ids=participant_ids
        )
        .filter(timestamp__gte=start, timestamp__lt=end, experiment__isnull=False)
        .values("experiment_id")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
    )
    return {row["experiment_id"]: row["cost"] for row in rows}


def session_usage(session: ExperimentSession) -> SessionUsage:
    """Cost/token breakdown by model for a single session, plus the overall
    total. Rows are ordered by descending cost. Uses the
    `(team, session, timestamp)` index.
    """
    rows = (
        UsageRecord.objects.filter(team_id=session.team_id, session=session)
        .values("model_name")
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            tokens=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
        )
        .order_by("-cost")
    )
    by_model = [
        ModelSpend(model_name=row["model_name"], cost=row["cost"], tokens=int(row["tokens"] or 0)) for row in rows
    ]
    total_cost = sum((row.cost for row in by_model), _ZERO)
    return SessionUsage(total_cost=total_cost, by_model=by_model)


def coverage_gaps(
    team: Team,
    *,
    start: datetime,
    end: datetime,
    experiment_ids: list[int] | None = None,
    platform_names: list[str] | None = None,
    participant_ids: list[int] | None = None,
) -> CoverageGaps:
    """The models behind the period's unpriced / no-usage warnings, so the
    panel can list which models are responsible. Single grouped query over the
    `(team, model_name, timestamp)` index; buckets with a zero count in a
    category are dropped. Each list is sorted by call count, descending.
    """
    period_q = Q(timestamp__gte=start, timestamp__lt=end)
    rows = (
        _scoped_records(
            team, experiment_ids=experiment_ids, platform_names=platform_names, participant_ids=participant_ids
        )
        .filter(period_q & (Q(confidence=Confidence.UNKNOWN) | Q(pricing_rule__isnull=True)))
        .values("provider_type", "model_name")
        .annotate(
            unknown_count=Count("id", filter=Q(confidence=Confidence.UNKNOWN)),
            unpriced_count=Count("id", filter=Q(pricing_rule__isnull=True) & ~Q(confidence=Confidence.UNKNOWN)),
        )
        .order_by()
    )
    unpriced, unknown = [], []
    for row in rows:
        if row["unpriced_count"]:
            unpriced.append(_coverage_gap_from_row(row, row["unpriced_count"]))
        if row["unknown_count"]:
            unknown.append(_coverage_gap_from_row(row, row["unknown_count"]))
    unpriced.sort(key=lambda gap: gap.call_count, reverse=True)
    unknown.sort(key=lambda gap: gap.call_count, reverse=True)
    return CoverageGaps(unpriced=unpriced, unknown=unknown)


def cost_timeseries(
    team: Team,
    *,
    start: datetime,
    end: datetime,
    granularity: str = "daily",
    experiment_ids: list[int] | None = None,
    platform_names: list[str] | None = None,
    participant_ids: list[int] | None = None,
) -> list[dict]:
    """Spend per time bucket in [start, end), ordered chronologically. Costs
    are returned as floats for direct JSON/Chart.js consumption. Empty buckets
    (no usage) are absent - the chart plots what's recorded.
    """
    trunc = _GRANULARITY_TRUNC.get(granularity, TruncDate)
    rows = (
        _scoped_records(
            team, experiment_ids=experiment_ids, platform_names=platform_names, participant_ids=participant_ids
        )
        .filter(timestamp__gte=start, timestamp__lt=end)
        .annotate(bucket=trunc("timestamp"))
        .values("bucket")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
        .order_by("bucket")
    )
    return [{"date": row["bucket"], "cost": float(row["cost"])} for row in rows]


def _coverage_gap_from_row(row: dict, call_count: int) -> ModelCoverageGap:
    return ModelCoverageGap(
        provider_type=row["provider_type"],
        model_name=row["model_name"],
        call_count=call_count,
    )


def _safe_pct(delta: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    return float(delta / previous * 100)
