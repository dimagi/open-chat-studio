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

from apps.cost_tracking.models import Confidence, ServiceKind, UsageRecord, UsageSource
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.models import Team
from apps.trace.models import Trace
from apps.usage_metrics.dashboard_querysets import filtered_querysets
from apps.usage_metrics.filters import chat_tag_exists_pair, conversation_messages

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
    """Period-over-period rollup for the dashboard panel.

    `total_cost` is every source — it's what the team actually spent, evaluations
    included, with no per-source split (ADR-0048).

    `estimated_call_count` is a row count, not derived from `estimated_cost` - a $0 estimated
    row (e.g. a zero-priced model) must still register as "estimated" for a confidence badge,
    and a Decimal `0` is falsy so `estimated_cost` alone can't tell "no estimated usage" apart
    from "estimated usage that happens to cost nothing".
    """

    period_start: datetime
    period_end: datetime
    total_cost: Decimal
    previous_period_cost: Decimal
    delta_pct: float | None
    exact_cost: Decimal
    estimated_cost: Decimal
    estimated_call_count: int
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
    """Per-model spend row for a single session's usage breakdown. `has_unpriced`/`has_estimated`/
    `has_unknown` mirror the trace detail page's per-model flags (`ModelTokens`) but scoped to this
    model's rows within the session."""

    model_name: str
    cost: Decimal
    tokens: int
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool


@dataclass(frozen=True)
class SessionUsage:
    """Whole-session usage: total cost plus a per-model breakdown, aggregated across every trace
    in the session. `has_unpriced` is what tells the session page whether `total_cost` is complete
    or partial - and, when the session has usage but `total_cost` is still zero, whether to render
    "no pricing data" rather than "$0.00". `has_estimated`/`has_unknown` drive the confidence badge,
    the same as the trace detail page's `TraceTokenUsage`."""

    total_cost: Decimal
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool
    by_model: list[ModelSpend]

    @property
    def total_tokens(self) -> int:
        return sum(row.tokens for row in self.by_model)


@dataclass(frozen=True)
class ModelTokens:
    """One (provider, model) row of a trace's token breakdown. `input_tokens` is fresh input only —
    `cached_input_tokens` and `cache_write_tokens` are the sub-buckets the recorder peels off it, so
    the row's input side is the sum of all three. `cost`/`has_unpriced`/`has_estimated`/`has_unknown`
    mirror the trace-level fields on `TraceTokenUsage` but scoped to this model's rows."""

    provider_type: str
    model_name: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    cost: Decimal
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cached_input_tokens + self.cache_write_tokens


@dataclass(frozen=True)
class TraceTokenUsage:
    """A single trace's token usage: the input/output headline plus a per-model breakdown.
    An empty `by_model` means no usage was recorded for the trace at all, which the trace
    detail page renders as "no data" rather than as zero.

    The headline is a two-way split, so unlike the usage API's `TokenCounts` it folds cache-write
    into `input_tokens`: every kind lands on one side or the other, which keeps
    `input_tokens + output_tokens == total` and reproduces the provider's headline input count.

    `total_cost` sums only priced rows (an unpriced row's `cost` defaults to 0), so `has_unpriced`
    is what tells the trace detail page whether that total is complete or partial - and, when the
    trace has usage but `total_cost` is still zero, whether to render "no pricing data" rather than
    "$0.00". `has_estimated`/`has_unknown` say whether any row's token count is a
    `Confidence.ESTIMATED` or `Confidence.UNKNOWN` guess rather than an exact provider-reported
    count, for the confidence badge."""

    input_tokens: int
    output_tokens: int
    total: int
    total_cost: Decimal
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool
    by_model: list[ModelTokens]


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
    reporting functions take one argument instead of four parallel lists.
    Tags match via the record's session's chat - a record counts when the
    chat or any of its messages carries one of the tags - so records with
    no session are excluded under a tag filter.
    """

    experiment_ids: list[int] | None = None
    platform_names: list[str] | None = None
    participant_ids: list[int] | None = None
    tag_ids: list[int] | None = None

    @property
    def narrows_to_entities(self) -> bool:
        """True when any filter restricts the read to particular chatbots, participants
        or conversations — which makes the result per-entity attribution, not a team
        total, and so subject to the chat-only rule (ADR-0048)."""
        return bool(self.experiment_ids or self.platform_names or self.participant_ids or self.tag_ids)


@dataclass(frozen=True)
class GroupBreakdown:
    """How ``usage_by_group`` slices records into rows, bundled so the function takes one argument
    instead of four parallel ones: the ``field`` to group by and the ``keys`` to keep, plus an optional
    tz-aware time bucketing (``granularity``/``tz``) that expands each group into one row per bucket."""

    field: str
    keys: list
    granularity: str | None = None
    tz: ZoneInfo | None = None


def _scoped_records(team: Team, filters: CostFilters | None = None):
    """Team-scoped UsageRecords with the dashboard's chatbot / participant /
    platform / tag filters applied (mirrors the cost panel's other charts).
    Platform and tags are matched via the record's session, so records with no
    session are excluded when either filter is set. A tag matches when the
    session's chat or any message in it carries the tag - the same semantics
    as the dashboard's session tag filter (`apps/usage_metrics/dashboard_querysets.py`).
    Both the link row and its tag must belong to the reading team, so a
    cross-team `CustomTaggedItem` row - whether its own `team_id` is foreign or
    its `tag` belongs to another team - never widens the read.

    A filtered read is per-entity attribution, so it counts chat only; only an
    unfiltered read is a team total and counts every source (ADR-0048). Without that,
    a dashboard narrowed to one chatbot would bill it for the judge calls that
    evaluated it, since those rows carry its `experiment_id`.
    """
    filters = filters or CostFilters()
    qs = UsageRecord.objects.filter(team=team)
    if filters.experiment_ids:
        qs = qs.filter(experiment_id__in=filters.experiment_ids)
    if filters.participant_ids:
        qs = qs.filter(participant_id__in=filters.participant_ids)
    if filters.platform_names:
        qs = qs.filter(session__platform__in=filters.platform_names)
    if filters.tag_ids:
        # The same chat-or-message match as the dashboard's tag filter, from
        # its single definition in apps.usage_metrics.
        tag_on_chat, tag_on_msg = chat_tag_exists_pair(team, filters.tag_ids, "session__chat_id")
        qs = qs.annotate(_tag_on_chat=tag_on_chat, _tag_on_msg=tag_on_msg).filter(
            Q(_tag_on_chat=True) | Q(_tag_on_msg=True)
        )
    if filters.narrows_to_entities:
        qs = qs.filter(source=UsageSource.CHAT)
    return qs


def _attributable_records(team: Team, filters: CostFilters | None = None):
    """Scoped records that may be charged to a single entity — chat only.

    Evaluation spend is the team's spend but never a chatbot's, a participant's or a
    conversation's (ADR-0048), so every read that names an entity goes through here
    (grouping) or gets the same treatment from `_scoped_records` (filtering), while an
    unfiltered team total counts every source. Eval rows do carry an experiment/session —
    always for generation (ADR-0050), and for the judge calls scoring it — so this filters
    on `source` rather than on those columns being null.
    """
    return _scoped_records(team, filters).filter(source=UsageSource.CHAT)


def cost_summary(team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None) -> CostSummary:
    """Total cost in [start, end), delta vs the equal-length prior period,
    and a confidence breakdown so the dashboard footer can show what share
    of the spend is estimated.
    """
    previous_start = start - (end - start)
    period_q = Q(timestamp__gte=start, timestamp__lt=end)
    previous_q = Q(timestamp__gte=previous_start, timestamp__lt=start)

    # Bound the scan to the two periods the aggregates below actually read.
    # Without it this scans the team's whole UsageRecord history on every load.
    agg = (
        _scoped_records(team, filters)
        .filter(timestamp__gte=previous_start, timestamp__lt=end)
        .aggregate(
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
            estimated_rows=Count("id", filter=period_q & Q(confidence=Confidence.ESTIMATED)),
            unknown_rows=Count("id", filter=period_q & Q(confidence=Confidence.UNKNOWN)),
            # Rows that got recorded but the resolver couldn't price (no matching
            # PricingRule). Excludes UNKNOWN-confidence rows because those have
            # their own counter. Distinct row counter, not a sum - these rows
            # contribute $0 to total_cost.
            unpriced_rows=Count(
                "id", filter=period_q & Q(pricing_rule__isnull=True) & ~Q(confidence=Confidence.UNKNOWN)
            ),
        )
    )

    return CostSummary(
        period_start=start,
        period_end=end,
        total_cost=agg["total"],
        previous_period_cost=agg["previous"],
        delta_pct=_safe_pct(agg["total"] - agg["previous"], agg["previous"]),
        exact_cost=agg["exact"],
        estimated_cost=agg["estimated"],
        estimated_call_count=agg["estimated_rows"],
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
    null experiment (e.g. trace whose experiment was hard-deleted) are excluded,
    as is evaluation spend — neither judging a chatbot nor exercising it from an
    eval run is the chatbot's cost.
    """
    rows = (
        _attributable_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end, experiment__isnull=False)
        .values("experiment_id")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
    )
    return {row["experiment_id"]: row["cost"] for row in rows}


@dataclass(frozen=True)
class ChatbotUsageSummary:
    """The chatbot home page's usage widget: a window's cost plus session/message counts for one
    chatbot. `cost` is `cost_summary` narrowed to this one experiment via `CostFilters`, so it
    carries the same exact/estimated split and coverage counts the dashboard panel shows."""

    cost: CostSummary
    sessions_count: int
    messages_count: int


def chatbot_usage_summary(experiment: Experiment, *, start: datetime, end: datetime) -> ChatbotUsageSummary:
    """Cost, session count and message count for one chatbot in [start, end), for the chatbot home
    page's usage widget. Session/message counts come from `filtered_querysets` - the same canonical,
    ADR-0051 activity definitions the dashboard's Bot Performance table uses - narrowed to this
    experiment, rather than re-deriving the session base here.
    """
    cost = cost_summary(experiment.team, start=start, end=end, filters=CostFilters(experiment_ids=[experiment.id]))
    querysets = filtered_querysets(experiment.team, start_date=start, end_date=end, experiment_ids=[experiment.id])
    sessions_count = querysets["sessions"].count()
    messages_count = conversation_messages(querysets["messages"]).count()
    return ChatbotUsageSummary(cost=cost, sessions_count=sessions_count, messages_count=messages_count)


def session_usage(session: ExperimentSession) -> SessionUsage:
    """Cost/token breakdown by model for a single session, plus the overall
    total. Rows are ordered by descending cost. Uses the
    `(team, session, timestamp)` index.

    Chat only, so an eval session totals $0 — since ADR-0050 both halves of its spend
    (generation and judging) are `source=EVALUATION`.
    """
    rows = (
        UsageRecord.objects.filter(team_id=session.team_id, session=session, source=UsageSource.CHAT)
        .values("model_name")
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            tokens=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
            unpriced_count=Count("id", filter=Q(pricing_rule__isnull=True)),
            estimated_count=Count("id", filter=Q(confidence=Confidence.ESTIMATED)),
            unknown_count=Count("id", filter=Q(confidence=Confidence.UNKNOWN)),
        )
        .order_by("-cost")
    )
    by_model = [
        ModelSpend(
            model_name=row["model_name"],
            cost=row["cost"],
            tokens=int(row["tokens"] or 0),
            has_unpriced=bool(row["unpriced_count"]),
            has_estimated=bool(row["estimated_count"]),
            has_unknown=bool(row["unknown_count"]),
        )
        for row in rows
    ]
    total_cost = sum((row.cost for row in by_model), _ZERO)
    return SessionUsage(
        total_cost=total_cost,
        has_unpriced=any(row.has_unpriced for row in by_model),
        has_estimated=any(row.has_estimated for row in by_model),
        has_unknown=any(row.has_unknown for row in by_model),
        by_model=by_model,
    )


def trace_token_usage(trace: Trace) -> TraceTokenUsage:
    """Token usage for a single trace, grouped by (provider, model).

    This is the source the trace detail page reads now that the counts no longer live on the
    `Trace` row. It covers more than those counters did — estimated calls are recorded here too
    — and splits by model, which a per-trace total could not.

    Scoped by `trace` alone (a trace id is unique, and the FK is indexed); the caller has already
    team-scoped the trace, and `Trace.team` is nullable while `UsageRecord.team` is not, so
    re-filtering on it would drop rows for a trace whose team was deleted.
    """
    rows = (
        UsageRecord.objects.filter(trace=trace)
        .values("provider_type", "model_name")
        .annotate(
            input_tokens=_kind_quantity(ServiceKind.LLM_INPUT),
            cached_input_tokens=_kind_quantity(ServiceKind.LLM_CACHED_INPUT),
            cache_write_tokens=_kind_quantity(ServiceKind.LLM_CACHE_WRITE),
            output_tokens=_kind_quantity(ServiceKind.LLM_OUTPUT),
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            unpriced_count=Count("id", filter=Q(pricing_rule__isnull=True)),
            estimated_count=Count("id", filter=Q(confidence=Confidence.ESTIMATED)),
            unknown_count=Count("id", filter=Q(confidence=Confidence.UNKNOWN)),
        )
        .order_by("provider_type", "model_name")
    )
    by_model = [
        ModelTokens(
            provider_type=row["provider_type"],
            model_name=row["model_name"],
            input_tokens=int(row["input_tokens"]),
            cached_input_tokens=int(row["cached_input_tokens"]),
            cache_write_tokens=int(row["cache_write_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cost=row["cost"],
            has_unpriced=bool(row["unpriced_count"]),
            has_estimated=bool(row["estimated_count"]),
            has_unknown=bool(row["unknown_count"]),
        )
        for row in rows
    ]
    input_tokens = sum(row.total_input_tokens for row in by_model)
    output_tokens = sum(row.output_tokens for row in by_model)
    return TraceTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total=input_tokens + output_tokens,
        total_cost=sum((row.cost for row in by_model), _ZERO),
        has_unpriced=any(row.has_unpriced for row in by_model),
        has_estimated=any(row.has_estimated for row in by_model),
        has_unknown=any(row.has_unknown for row in by_model),
        by_model=by_model,
    )


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
    """Spend per time bucket in [start, end), split by source, ordered chronologically.

    One row per non-empty bucket: ``{'date': bucket, <source>: float, ...}``, carrying a
    key for every source the read counts, zero-filled so the chart's stacked series stay
    aligned. An unfiltered read is a team total and so carries every source; a filtered one
    is per-entity attribution and so carries `chat` alone (ADR-0048) - the evaluation key is
    absent there rather than a misleading zero. Costs are floats for direct JSON/Chart.js
    consumption. Empty buckets (no usage) are absent - the chart plots what's recorded.
    """
    filters = filters or CostFilters()
    sources = [UsageSource.CHAT.value] if filters.narrows_to_entities else [source.value for source in UsageSource]
    trunc = _GRANULARITY_TRUNC.get(granularity, TruncDate)
    rows = (
        _scoped_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end)
        .annotate(bucket=trunc("timestamp"))
        .values("bucket", "source")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
        .order_by("bucket")
    )
    buckets: dict = {}
    for row in rows:
        point = buckets.setdefault(row["bucket"], {"date": row["bucket"], **dict.fromkeys(sources, 0.0)})
        point[row["source"]] = float(row["cost"])
    return list(buckets.values())


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
    dashboard's Chart.js series is ``cost_timeseries`` (float, UTC-bucketed, split by source).
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
    breakdown: GroupBreakdown,
    resolve_currency: bool = True,
    filters: CostFilters | None = None,
) -> list[dict]:
    """Cost + token counts in [start, end) grouped by ``breakdown.field`` (``participant_id`` /
    ``experiment_id`` / ``session__platform``), restricted to ``breakdown.keys``. One row per group — or
    per (group, bucket) when ``breakdown.granularity`` is set, truncated in ``breakdown.tz``. Each row is
    ``{'key', ['bucket'], 'cost' (Decimal), 'currency', 'prompt', 'completion', 'total'}``. Shares the
    scoped-record path with ``cost_total``/``token_counts`` (same team + ``CostFilters`` scoping), but
    counts chat spend only — every group here is an entity, and evaluation spend is not any entity's
    (ADR-0048); the caller zero-fills groups/buckets absent from the result. Note the per-group rows need
    not sum to the ungrouped window total: evaluation records, and records whose ``group_field`` is NULL
    (e.g. a session-less record under platform grouping) or falls outside ``keys``, are all excluded from
    the breakdown.

    ``resolve_currency=False`` skips the extra ``SELECT DISTINCT currency`` scan when the caller only
    wants token counts; ``currency`` then defaults to ``"USD"`` (unused by a tokens-only caller).
    """
    scoped = (
        _attributable_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end, **{f"{breakdown.field}__in": breakdown.keys})
        .annotate(key=F(breakdown.field))
    )
    currency = _single_currency(scoped) if resolve_currency else "USD"
    group_cols = ["key"]
    if breakdown.granularity:
        trunc = _GRANULARITY_TRUNC.get(breakdown.granularity, TruncDate)
        scoped = scoped.annotate(bucket=trunc("timestamp", tzinfo=breakdown.tz))
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


def _kind_quantity(kind: ServiceKind):
    """Summed `quantity` for one service kind, zero when the group has no such rows."""
    return Coalesce(Sum("quantity", filter=Q(service_kind=kind)), _ZERO, output_field=_QUANTITY_FIELD)


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
