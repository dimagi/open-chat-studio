"""Read path for cost tracking. The dashboard, REST endpoints, and weekly
digest all consume this. Aggregations are single-query, team-scoped, and
hit the `(team, timestamp)` / `(team, experiment, timestamp)` indexes.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek

from apps.cost_tracking.models import Confidence, ServiceKind, UsageRecord
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

# Token split for the usage API: `prompt` covers fresh + cached input, `completion` is output, and
# `total` is every LLM kind (so cache-write tokens land in the total but neither sub-count).
_PROMPT_KINDS = (ServiceKind.LLM_INPUT, ServiceKind.LLM_CACHED_INPUT)


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
class TokenCounts:
    """Prompt / completion / total token counts for the usage API, summed from ``UsageRecord.quantity``
    and split by ``service_kind``. ``prompt + completion`` need not equal ``total`` — cache-write tokens
    are in ``total`` only."""

    prompt: int
    completion: int
    total: int


@dataclass(frozen=True)
class CostTotal:
    """Total priced spend for a window plus its currency, for the usage API."""

    total: Decimal
    currency: str


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


@dataclass(frozen=True)
class CostFilters:
    """The dashboard filters the cost read path honours, bundled so the
    reporting functions take one argument instead of three parallel lists.
    Tags are intentionally absent - usage records aren't tagged directly.
    """

    experiment_ids: list[int] | None = None
    platform_names: list[str] | None = None
    participant_ids: list[int] | None = None


def _scoped_records(team: Team, filters: CostFilters | None = None):
    """Team-scoped UsageRecords with the dashboard's chatbot / participant /
    platform filters applied (mirrors the cost panel's other charts). Platform
    is matched via the record's session, so records with no session are excluded
    when a platform filter is set.
    """
    filters = filters or CostFilters()
    qs = UsageRecord.objects.filter(team=team)
    if filters.experiment_ids:
        qs = qs.filter(experiment_id__in=filters.experiment_ids)
    if filters.participant_ids:
        qs = qs.filter(participant_id__in=filters.participant_ids)
    if filters.platform_names:
        qs = qs.filter(session__platform__in=filters.platform_names)
    return qs


def cost_summary(team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None) -> CostSummary:
    """Total cost in [start, end), delta vs the equal-length prior period,
    and a confidence breakdown so the dashboard footer can show what share
    of the spend is estimated.
    """
    previous_start = start - (end - start)
    period_q = Q(timestamp__gte=start, timestamp__lt=end)
    previous_q = Q(timestamp__gte=previous_start, timestamp__lt=start)

    agg = _scoped_records(team, filters).aggregate(
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


def token_counts(team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None) -> TokenCounts:
    """Token usage in [start, end), summed from ``UsageRecord.quantity`` and split by ``service_kind``.
    Shares the scoped-record path (team + ``CostFilters``) with ``cost_summary`` so tokens and cost for
    the same window reconcile against the same rows.
    """
    agg = (
        _scoped_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end)
        .aggregate(
            prompt=Coalesce(
                Sum("quantity", filter=Q(service_kind__in=_PROMPT_KINDS)), _ZERO, output_field=_QUANTITY_FIELD
            ),
            completion=Coalesce(
                Sum("quantity", filter=Q(service_kind=ServiceKind.LLM_OUTPUT)), _ZERO, output_field=_QUANTITY_FIELD
            ),
            total=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
        )
    )
    return TokenCounts(prompt=int(agg["prompt"]), completion=int(agg["completion"]), total=int(agg["total"]))


def cost_total(team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None) -> CostTotal:
    """Total priced spend in [start, end) and its currency, in a single grouped query. This is the
    lightweight read the usage API needs: it shares the scoped-record path with ``token_counts`` (so
    cost and tokens reconcile), but unlike ``cost_summary`` it skips the prior-period scan and the
    confidence/coverage aggregates the dashboard needs and the API discards.

    OCS is effectively single-currency, so the currency is the one present; with no records (or,
    defensively, a mix) it falls back to ``"USD"`` — the same default the pricing layer uses.
    """
    rows = list(
        _scoped_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end)
        .values("currency")
        .annotate(total=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
        .order_by()
    )
    total = sum((row["total"] for row in rows), _ZERO)
    currency = rows[0]["currency"] if len(rows) == 1 else "USD"
    return CostTotal(total=total, currency=currency)


def costs_by_experiment(
    team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None
) -> dict[int, Decimal]:
    """Total cost per experiment in the period, keyed by `experiment_id`.
    Feeds the dashboard's Bot Performance table cost column. Records with a
    null experiment (e.g. trace whose experiment was hard-deleted) are excluded.
    """
    rows = (
        _scoped_records(team, filters)
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


def coverage_gaps(team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None) -> CoverageGaps:
    """The models behind the period's unpriced / no-usage warnings, so the
    panel can list which models are responsible. Single grouped query over the
    `(team, model_name, timestamp)` index; buckets with a zero count in a
    category are dropped. Each list is sorted by call count, descending.
    """
    period_q = Q(timestamp__gte=start, timestamp__lt=end)
    rows = (
        _scoped_records(team, filters)
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
    team: Team, *, start: datetime, end: datetime, granularity: str = "daily", filters: CostFilters | None = None
) -> list[dict]:
    """Spend per time bucket in [start, end), ordered chronologically. Costs
    are returned as floats for direct JSON/Chart.js consumption. Empty buckets
    (no usage) are absent - the chart plots what's recorded.
    """
    trunc = _GRANULARITY_TRUNC.get(granularity, TruncDate)
    rows = (
        _scoped_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end)
        .annotate(bucket=trunc("timestamp"))
        .values("bucket")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
        .order_by("bucket")
    )
    return [{"date": row["bucket"], "cost": float(row["cost"])} for row in rows]


def usage_timeseries(
    team: Team,
    *,
    start: datetime,
    end: datetime,
    granularity: str,
    tz: ZoneInfo,
    filters: CostFilters | None = None,
) -> list[dict]:
    """Cost + token counts per time bucket in [start, end), truncated in ``tz``. One row per non-empty
    bucket: ``{'bucket', 'cost' (Decimal), 'currency', 'prompt', 'completion', 'total'}``. Empty buckets
    are absent (the caller zero-fills). Shares the scoped-record path with ``cost_total``/``token_counts``
    so a bucketed usage response reconciles with the same window's totals. This is the API read; the
    dashboard's Chart.js series is ``cost_timeseries`` (float, UTC-bucketed).
    """
    trunc = _GRANULARITY_TRUNC.get(granularity, TruncDate)
    scoped = _scoped_records(team, filters).filter(timestamp__gte=start, timestamp__lt=end)
    currency = _single_currency(scoped)
    rows = (
        scoped.annotate(bucket=trunc("timestamp", tzinfo=tz))
        .values("bucket")
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            prompt=Coalesce(
                Sum("quantity", filter=Q(service_kind__in=_PROMPT_KINDS)), _ZERO, output_field=_QUANTITY_FIELD
            ),
            completion=Coalesce(
                Sum("quantity", filter=Q(service_kind=ServiceKind.LLM_OUTPUT)), _ZERO, output_field=_QUANTITY_FIELD
            ),
            total=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
        )
        .order_by("bucket")
    )
    return [
        {
            "bucket": row["bucket"],
            "cost": row["cost"],
            "currency": currency,
            "prompt": int(row["prompt"]),
            "completion": int(row["completion"]),
            "total": int(row["total"]),
        }
        for row in rows
    ]


def usage_by_group(
    team: Team,
    *,
    start: datetime,
    end: datetime,
    group_field: str,
    keys: list,
    granularity: str | None = None,
    tz: ZoneInfo | None = None,
    resolve_currency: bool = True,
    filters: CostFilters | None = None,
) -> list[dict]:
    """Cost + token counts in [start, end) grouped by ``group_field`` (``participant_id`` /
    ``experiment_id`` / ``session__platform``), restricted to ``keys``. One row per group — or per
    (group, bucket) when ``granularity`` is set, truncated in ``tz``. Each row is
    ``{'key', ['bucket'], 'cost' (Decimal), 'currency', 'prompt', 'completion', 'total'}``. Shares the
    scoped-record path with ``cost_total``/``token_counts`` (same team + ``CostFilters`` scoping); the
    caller zero-fills groups/buckets absent from the result. Note the per-group rows need not sum to the
    ungrouped window total: records whose ``group_field`` is NULL (e.g. a session-less record under
    platform grouping) or falls outside ``keys`` are excluded from the breakdown.

    ``resolve_currency=False`` skips the extra ``SELECT DISTINCT currency`` scan when the caller only
    wants token counts; ``currency`` then defaults to ``"USD"`` (unused by a tokens-only caller).
    """
    scoped = (
        _scoped_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end, **{f"{group_field}__in": keys})
        .annotate(key=F(group_field))
    )
    currency = _single_currency(scoped) if resolve_currency else "USD"
    group_cols = ["key"]
    if granularity:
        trunc = _GRANULARITY_TRUNC.get(granularity, TruncDate)
        scoped = scoped.annotate(bucket=trunc("timestamp", tzinfo=tz))
        group_cols.append("bucket")
    rows = (
        scoped.values(*group_cols)
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            prompt=Coalesce(
                Sum("quantity", filter=Q(service_kind__in=_PROMPT_KINDS)), _ZERO, output_field=_QUANTITY_FIELD
            ),
            completion=Coalesce(
                Sum("quantity", filter=Q(service_kind=ServiceKind.LLM_OUTPUT)), _ZERO, output_field=_QUANTITY_FIELD
            ),
            total=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
        )
        .order_by()
    )
    return [
        {
            "key": row["key"],
            "bucket": row.get("bucket"),
            "cost": row["cost"],
            "currency": currency,
            "prompt": int(row["prompt"]),
            "completion": int(row["completion"]),
            "total": int(row["total"]),
        }
        for row in rows
    ]


def _single_currency(scoped) -> str:
    """The one currency present in a scoped queryset, or ``"USD"`` when there are none or (defensively)
    a mix — the same single-currency assumption ``cost_total`` makes."""
    currencies = list(scoped.values_list("currency", flat=True).distinct())
    return currencies[0] if len(currencies) == 1 else "USD"


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
