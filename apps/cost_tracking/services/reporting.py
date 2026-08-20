"""Read path for cost tracking. The dashboard, REST endpoints, weekly digest, and the
evaluations UI all consume this. Aggregations are single-query, team-scoped, and hit
the `(team, timestamp)` / `(team, experiment, timestamp)` indexes.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.cost_tracking.models import Confidence, ServiceKind, UsageRecord, UsageSource
from apps.evaluations.models import EvaluationConfig, EvaluationRun, Evaluator
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

# The chatbot home usage widget's rolling window and how long its cached snapshot is trusted for -
# see `get_latest_chatbot_usage_summary`.
_CHATBOT_USAGE_SUMMARY_WINDOW_DAYS = 30
_CHATBOT_USAGE_SUMMARY_CACHE_TTL_SECONDS = 5 * 60


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


def _entity_cost_map(
    team: Team, field: str, *, start: datetime, end: datetime, filters: CostFilters | None
) -> dict[int, Decimal]:
    """Chat-only cost grouped by an entity FK (`experiment` / `participant`),
    keyed by the FK id, rows with a null FK excluded."""
    rows = (
        _attributable_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end, **{f"{field}__isnull": False})
        .values(f"{field}_id")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
    )
    return {row[f"{field}_id"]: row["cost"] for row in rows}


def costs_by_experiment(
    team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None
) -> dict[int, Decimal]:
    """Total cost per experiment in the period, keyed by `experiment_id`.
    Feeds the dashboard's Bot Performance table cost column. Records with a
    null experiment (e.g. trace whose experiment was hard-deleted) are excluded,
    as is evaluation spend — neither judging a chatbot nor exercising it from an
    eval run is the chatbot's cost.
    """
    return _entity_cost_map(team, "experiment", start=start, end=end, filters=filters)


def costs_by_participant(
    team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None
) -> dict[int, Decimal]:
    """Total cost per participant in the period, keyed by `participant_id`.
    Feeds the dashboard's Most Active Participants table and the participants
    page cost column; both bound the read to a handful of participants via
    `CostFilters.participant_ids` since `UsageRecord` has no `(team, participant)`
    index. Records with a null participant are excluded, as is evaluation spend -
    per-entity attribution is chat-only (ADR-0048).
    """
    return _entity_cost_map(team, "participant", start=start, end=end, filters=filters)


def costs_by_model(team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None) -> list[dict]:
    """Cost per (provider_type, model_name) in [start, end), ordered by descending
    cost, as floats for direct JSON/Chart.js consumption. One grouped query over
    the `(team, timestamp)` index; it feeds both of the dashboard's
    provider and model charts - the provider chart sums these rows per provider
    client-side. An unfiltered read is a team total and counts every source; a
    filtered read is narrowed to chat by `_scoped_records` (ADR-0048).
    """
    rows = (
        _scoped_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end)
        .values("provider_type", "model_name")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
        .order_by("-cost", "provider_type", "model_name")
    )
    return [
        {"provider_type": row["provider_type"], "model_name": row["model_name"], "cost": float(row["cost"])}
        for row in rows
    ]


def costs_by_service_kind(
    team: Team, *, start: datetime, end: datetime, filters: CostFilters | None = None
) -> list[dict]:
    """Cost and token quantity per ServiceKind in [start, end), one grouped query,
    JSON-shaped (float cost, integer tokens). Feeds the dashboard's service-kind
    donut, which toggles between the two measures; the cached-input slice is the
    "are we benefiting from caching" answer. Kinds with no rows are absent - the
    frontend zero-fills over the fixed four-kind order. Same ADR-0048 source
    semantics as `costs_by_model`.
    """
    rows = (
        _scoped_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end)
        .values("service_kind")
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            tokens=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
        )
        .order_by()
    )
    return [
        {"service_kind": row["service_kind"], "cost": float(row["cost"]), "tokens": int(row["tokens"])} for row in rows
    ]


# Series cap for the p95 chart. The chatbot filter still narrows the read to
# any specific bot regardless of the cap, so nothing is unreachable; tune the
# value against the rendered chart with real data.
P95_TOP_CHATBOTS = 5


def p95_cost_per_trace(
    team: Team,
    *,
    start: datetime,
    end: datetime,
    granularity: str = "daily",
    filters: CostFilters | None = None,
    top_n: int = P95_TOP_CHATBOTS,
) -> list[dict]:
    """p95 of per-trace cost, per chatbot per time bucket, for the dashboard's
    cost-per-trace chart. Per-trace cost is `Sum(cost)` grouped by
    (trace, experiment, bucket); the p95 is nearest-rank over those totals in
    Python. One series per chatbot, ordered by descending window spend, capped
    to `top_n`. Chat-only: the read attributes cost to chatbots, so it goes
    through `_attributable_records` (ADR-0048) - and traces only carry chat
    spend anyway (ADR-0050). Costs are floats for direct JSON/Chart.js use.

    The percentile is computed here in Python rather than in SQL; if that becomes a
    bottleneck at scale, Postgres's `percentile_cont` is the escape hatch.
    """
    trunc = _GRANULARITY_TRUNC.get(granularity, TruncDate)
    rows = (
        _attributable_records(team, filters)
        .filter(timestamp__gte=start, timestamp__lt=end, trace__isnull=False, experiment__isnull=False)
        .annotate(bucket=trunc("timestamp"))
        .values("trace_id", "experiment_id", "bucket")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
        .order_by()
    )
    per_bucket: dict[tuple, list[float]] = defaultdict(list)
    spend: dict[int, Decimal] = defaultdict(Decimal)
    for row in rows:
        per_bucket[(row["experiment_id"], row["bucket"])].append(float(row["cost"]))
        spend[row["experiment_id"]] += row["cost"]
    top = sorted(spend, key=lambda experiment_id: (-spend[experiment_id], experiment_id))[:top_n]
    # get_all() bypasses the versioning manager's default is_archived=False filter - a chatbot
    # archived after spending in the window must still resolve to its real name rather than a
    # blank legend entry, since the spend already happened and is charted regardless.
    names = dict(Experiment.objects.get_all().filter(team=team, id__in=top).values_list("id", "name")) if top else {}
    return [
        {
            "experiment_id": experiment_id,
            "experiment_name": names.get(experiment_id, ""),
            "points": [
                {"date": bucket, "p95": _nearest_rank_p95(per_bucket[(experiment_id, bucket)])}
                for bucket in sorted(bucket for key_id, bucket in per_bucket if key_id == experiment_id)
            ],
        }
        for experiment_id in top
    ]


def _nearest_rank_p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[max(math.ceil(0.95 * len(ordered)) - 1, 0)]


@dataclass(frozen=True)
class ChatbotUsageSummary:
    """The chatbot home page's usage widget: a window's cost plus session/message counts for one
    chatbot. `cost` is `cost_summary` narrowed to this one experiment via `CostFilters`, so it
    carries the same exact/estimated split and coverage counts the dashboard panel shows."""

    cost: CostSummary
    sessions_count: int
    messages_count: int


def chatbot_usage_summary(team: Team, experiment_id: int, *, start: datetime, end: datetime) -> ChatbotUsageSummary:
    """Cost, session count and message count for one chatbot in [start, end), for the chatbot home
    page's usage widget. Session/message counts come from `filtered_querysets` - the same canonical,
    ADR-0051 activity definitions the dashboard's Bot Performance table uses - narrowed to this
    experiment, rather than re-deriving the session base here.

    Takes `team` and `experiment_id` rather than an `Experiment` object - the query below only ever
    needs those two, and requiring the row would force `get_latest_chatbot_usage_summary` to fetch it
    even on a cache hit, defeating a chunk of the point of caching this at all.
    """
    cost = cost_summary(team, start=start, end=end, filters=CostFilters(experiment_ids=[experiment_id]))
    querysets = filtered_querysets(team, start_date=start, end_date=end, experiment_ids=[experiment_id])
    sessions_count = querysets["sessions"].count()
    messages_count = conversation_messages(querysets["messages"]).count()
    return ChatbotUsageSummary(cost=cost, sessions_count=sessions_count, messages_count=messages_count)


def _chatbot_usage_summary_cache_key(team_id: int, experiment_id: int) -> str:
    return f"cost:chatbot_usage_summary:{team_id}:{experiment_id}"


def get_latest_chatbot_usage_summary(team: Team, experiment_id: int) -> ChatbotUsageSummary:
    """Cost, session count and message count for one chatbot over the last
    `_CHATBOT_USAGE_SUMMARY_WINDOW_DAYS` days, for the chatbot home page's usage widget.

    This owns both the "latest N days" window and the caching, rather than leaving either to the
    view: the two are coupled (see `chatbot_usage_summary`'s docstring for why a `start`/`end`-taking
    function can't safely own this cache), and a cache hit here needs no query at all, since
    `chatbot_usage_summary` needs nothing but the `team`/`experiment_id` this function already has.
    Cached in Redis for `_CHATBOT_USAGE_SUMMARY_CACHE_TTL_SECONDS`: short enough that nobody
    watching the page during an active conversation would call the number stale, with no
    signal-based invalidation - `UsageRecord` rows are written continuously, so invalidating on
    write would defeat the cache (the same tradeoff `PricingResolver`'s cache makes).
    """
    cache_key = _chatbot_usage_summary_cache_key(team.id, experiment_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    end = timezone.now()
    start = end - timedelta(days=_CHATBOT_USAGE_SUMMARY_WINDOW_DAYS)
    usage = chatbot_usage_summary(team, experiment_id, start=start, end=end)

    cache.set(cache_key, usage, _CHATBOT_USAGE_SUMMARY_CACHE_TTL_SECONDS)
    return usage


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


@dataclass(frozen=True)
class EvaluationModelSpend:
    """One (provider, model) row of an evaluation run's cost breakdown."""

    provider_type: str
    model_name: str
    cost: Decimal
    tokens: int
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool


@dataclass(frozen=True)
class EvaluatorSpend:
    """One row of a run's cost broken down by what incurred it: an evaluator's judge
    calls, or (`evaluator_id=None`) the bot generation the run drove."""

    evaluator_id: int | None
    evaluator_name: str
    cost: Decimal
    tokens: int
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool


@dataclass(frozen=True)
class EvaluationRunCost:
    """One evaluation run's cost: the total plus two breakdowns of the same rows —
    by (provider, model) and by what incurred the spend (an evaluator, or generation)."""

    total_cost: Decimal
    total_tokens: int
    currency: str
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool
    by_evaluator: list[EvaluatorSpend] = field(default_factory=list)
    by_model: list[EvaluationModelSpend] = field(default_factory=list)


@dataclass(frozen=True)
class EvaluationPeriodCost:
    """One window's worth of `EvaluationConfigCostSummary` — cost plus the same
    confidence flags as `EvaluationRunCost`, scoped to that window rather than a run."""

    total_cost: Decimal
    has_unpriced: bool
    has_estimated: bool
    has_unknown: bool


@dataclass(frozen=True)
class EvaluationConfigCostSummary:
    """Aggregate spend across every run of one evaluation config, for the config page:
    last 30 days and all time, each with its own confidence flags since a model priced
    today may have been unpriced (or estimated) 45 days ago."""

    last_30_days: EvaluationPeriodCost
    all_time: EvaluationPeriodCost
    currency: str


def evaluation_run_cost(run: EvaluationRun) -> EvaluationRunCost:
    """Total cost for one evaluation run, broken down by (provider, model) and by
    evaluator. Both breakdowns and the currency are grouped/aggregated in the DB;
    only the totals — a sum/any() over a handful of already-aggregated model rows —
    happen in Python.

    Scoped by the indexed `evaluation_config` FK first; `extra.evaluation_run_id` (a
    JSON key, not a FK, since runs get pruned) narrows to the run within that. Every row
    scoped this way is `source=EVALUATION` by construction (`evaluation_config` is only
    ever set on evaluation rows), so this needs none of the source filtering the team-scoped
    reads above apply (ADR-0048) — it's already narrowed to exactly the evaluation spend
    the run detail page wants to show.
    """
    scoped = UsageRecord.objects.filter(evaluation_config_id=run.config_id, extra__evaluation_run_id=run.id)
    currency = _single_currency(scoped)

    model_rows = (
        scoped.values("provider_type", "model_name")
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
        EvaluationModelSpend(
            provider_type=row["provider_type"],
            model_name=row["model_name"],
            cost=row["cost"],
            tokens=int(row["tokens"]),
            has_unpriced=bool(row["unpriced_count"]),
            has_estimated=bool(row["estimated_count"]),
            has_unknown=bool(row["unknown_count"]),
        )
        for row in model_rows
    ]

    evaluator_rows = list(
        scoped.values("extra__evaluator_id")
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            tokens=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
            unpriced_count=Count("id", filter=Q(pricing_rule__isnull=True)),
            estimated_count=Count("id", filter=Q(confidence=Confidence.ESTIMATED)),
            unknown_count=Count("id", filter=Q(confidence=Confidence.UNKNOWN)),
        )
        .order_by("-cost")
    )
    evaluator_ids = [row["extra__evaluator_id"] for row in evaluator_rows if row["extra__evaluator_id"] is not None]
    evaluator_names = dict(Evaluator.objects.filter(id__in=evaluator_ids).values_list("id", "name"))
    by_evaluator = [
        EvaluatorSpend(
            evaluator_id=row["extra__evaluator_id"],
            evaluator_name=(
                evaluator_names.get(row["extra__evaluator_id"], f"Evaluator {row['extra__evaluator_id']}")
                if row["extra__evaluator_id"] is not None
                else "Bot generation"
            ),
            cost=row["cost"],
            tokens=int(row["tokens"]),
            has_unpriced=bool(row["unpriced_count"]),
            has_estimated=bool(row["estimated_count"]),
            has_unknown=bool(row["unknown_count"]),
        )
        for row in evaluator_rows
    ]

    return EvaluationRunCost(
        total_cost=sum((row.cost for row in by_model), _ZERO),
        total_tokens=sum(row.tokens for row in by_model),
        currency=currency,
        has_unpriced=any(row.has_unpriced for row in by_model),
        has_estimated=any(row.has_estimated for row in by_model),
        has_unknown=any(row.has_unknown for row in by_model),
        by_evaluator=by_evaluator,
        by_model=by_model,
    )


def evaluation_run_costs(config_id: int, run_ids: list[int]) -> dict[int, Decimal]:
    """Total cost per run, keyed by run id, for a page of runs in one config.

    Filters on both the indexed `evaluation_config` FK and `extra.evaluation_run_id__in`,
    so the GROUP BY is bounded to the runs asked for rather than the whole config's
    history — every row the query touches already has its run id in `run_ids`, so no
    further filtering is needed once the rows come back.
    """
    if not run_ids:
        return {}
    rows = (
        UsageRecord.objects.filter(evaluation_config_id=config_id, extra__evaluation_run_id__in=run_ids)
        .values("extra__evaluation_run_id")
        .annotate(cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD))
        .order_by()
    )
    return {row["extra__evaluation_run_id"]: row["cost"] for row in rows}


def evaluation_config_cost_summary(config: EvaluationConfig) -> EvaluationConfigCostSummary:
    """Aggregate spend across every run of one config: last 30 days and all time, each
    with its own confidence flags (mirrors `cost_summary`'s per-period counters).

    Single aggregate query — a cost sum plus three confidence counts per period — over
    the indexed `evaluation_config` FK.
    """
    qs = UsageRecord.objects.filter(evaluation_config_id=config.id)
    recent_q = Q(timestamp__gte=timezone.now() - timedelta(days=30))
    agg = qs.aggregate(
        all_time_cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
        all_time_unpriced=Count("id", filter=Q(pricing_rule__isnull=True)),
        all_time_estimated=Count("id", filter=Q(confidence=Confidence.ESTIMATED)),
        all_time_unknown=Count("id", filter=Q(confidence=Confidence.UNKNOWN)),
        last_30_days_cost=Coalesce(Sum("cost", filter=recent_q), _ZERO, output_field=_COST_FIELD),
        last_30_days_unpriced=Count("id", filter=recent_q & Q(pricing_rule__isnull=True)),
        last_30_days_estimated=Count("id", filter=recent_q & Q(confidence=Confidence.ESTIMATED)),
        last_30_days_unknown=Count("id", filter=recent_q & Q(confidence=Confidence.UNKNOWN)),
    )
    currency = _single_currency(qs)
    return EvaluationConfigCostSummary(
        last_30_days=EvaluationPeriodCost(
            total_cost=agg["last_30_days_cost"],
            has_unpriced=bool(agg["last_30_days_unpriced"]),
            has_estimated=bool(agg["last_30_days_estimated"]),
            has_unknown=bool(agg["last_30_days_unknown"]),
        ),
        all_time=EvaluationPeriodCost(
            total_cost=agg["all_time_cost"],
            has_unpriced=bool(agg["all_time_unpriced"]),
            has_estimated=bool(agg["all_time_estimated"]),
            has_unknown=bool(agg["all_time_unknown"]),
        ),
        currency=currency,
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
