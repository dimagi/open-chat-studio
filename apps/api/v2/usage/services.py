"""Query orchestration for the usage API (``GET /api/v2/usage/``).

This is the single place that turns a validated ``UsageQuery`` into team-scoped aggregates. It
supports the ``messages``, ``sessions``, ``participants``, ``cost``, and ``tokens`` metrics over an
explicit ``[start, end)`` window (resolved from ``period`` or ``start``/``end`` by the param
serializer) at ``total``/``daily``/``weekly``/``monthly`` granularity. Later slices add grouping
without changing this contract. See ``docs/design/usage-api.md``.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TypedDict
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Q, QuerySet
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.cost_tracking.services import reporting
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus
from apps.teams.models import Team

# Metric identifiers. Later slices extend SUPPORTED_METRICS; the param serializer validates against it.
METRIC_MESSAGES = "messages"
METRIC_SESSIONS = "sessions"
METRIC_PARTICIPANTS = "participants"
METRIC_COST = "cost"
METRIC_TOKENS = "tokens"
SUPPORTED_METRICS = frozenset({METRIC_MESSAGES, METRIC_SESSIONS, METRIC_PARTICIPANTS, METRIC_COST, METRIC_TOKENS})

# Calendar-month period selectors.
PERIOD_CURRENT_MONTH = "current_month"
PERIOD_PREVIOUS_MONTH = "previous_month"
PERIOD_CHOICES = (PERIOD_CURRENT_MONTH, PERIOD_PREVIOUS_MONTH)

# Time-bucketing of results. ``total`` collapses the window to a single object; the others emit one
# row per bucket, truncated in the request timezone.
GRANULARITY_TOTAL = "total"
GRANULARITY_DAILY = "daily"
GRANULARITY_WEEKLY = "weekly"
GRANULARITY_MONTHLY = "monthly"
GRANULARITY_CHOICES = (GRANULARITY_TOTAL, GRANULARITY_DAILY, GRANULARITY_WEEKLY, GRANULARITY_MONTHLY)

# DB truncation per bucketed granularity (Django's TruncWeek starts weeks on Monday, matching the
# Python-side bucket boundaries below).
_TRUNC = {
    GRANULARITY_DAILY: TruncDate,
    GRANULARITY_WEEKLY: TruncWeek,
    GRANULARITY_MONTHLY: TruncMonth,
}
_STEP = {
    GRANULARITY_DAILY: relativedelta(days=1),
    GRANULARITY_WEEKLY: relativedelta(weeks=1),
    GRANULARITY_MONTHLY: relativedelta(months=1),
}


class MessageCounts(TypedDict):
    human: int
    ai: int
    total: int


class CostBlock(TypedDict):
    total: Decimal
    currency: str


class TokenCounts(TypedDict):
    prompt: int
    completion: int
    total: int


@dataclass(frozen=True)
class UsageResult:
    """The endpoint response, pre-serialisation.

    ``results`` is either a single object mapping each requested metric name to its aggregate block
    (``granularity=total``), or a list of such objects — one per time bucket, each carrying a
    ``bucket_start`` — when a finer granularity is requested. Grouping arrives in a later slice."""

    period_start: datetime
    period_end: datetime
    timezone: str
    granularity: str
    results: dict | list


@dataclass(frozen=True)
class UsageQuery:
    """Validated, team-scoped inputs for a single usage request. Bundling them keeps the many
    aggregation helpers to one parameter and lets the window/filters travel together unchanged.

    The ``participant``/``participant_identifier``/``chatbot`` fields are the raw request handles;
    :func:`resolve_query_filters` turns them into the ``*_ids`` below **once** per request. The
    queryset builders filter on those FK-id fields (never the handles), so no metric joins the
    ``experiment``/``participant`` tables and the archived-inclusive id resolution lives in one place.
    Build a query, then resolve it before running any aggregation."""

    team: Team
    metrics: set[str]
    start: datetime
    end: datetime
    tz: ZoneInfo
    granularity: str = GRANULARITY_TOTAL
    participant: str | None = None
    participant_identifier: str | None = None
    chatbot: str | None = None
    platform: str | None = None
    # Populated by resolve_query_filters. ``None`` means "no such filter requested"; an empty list means
    # "requested but matched nobody" (with filter_is_empty set), which the ``__in`` filters turn into an
    # empty result and the cost path short-circuits to zeros.
    participant_ids: list[int] | None = None
    experiment_ids: list[int] | None = None
    filter_is_empty: bool = False


@dataclass(frozen=True)
class _CostFilter:
    """A resolved participant filter for the cost read path. ``filters`` is passed to ``reporting``;
    ``is_empty`` is True when a participant filter was requested but matched no one, so the caller
    short-circuits to zeros (``CostFilters`` treats an empty id list as "no filter", not "match none")."""

    filters: reporting.CostFilters
    is_empty: bool


def resolve_query_filters(query: UsageQuery) -> UsageQuery:
    """Resolve the participant/chatbot request handles to DB ids **once**, returning a new query that
    carries them. Every metric then filters on the FK-id columns (``participant_id``/``experiment_id``)
    instead of joining ``participant``/``experiment`` to match a ``public_id``/``identifier``, and the
    archived-inclusive resolution (``get_all()``) happens in exactly one place. Call this before running
    any aggregation; ``filter_is_empty`` marks a requested filter that matched nobody so the reader
    returns zeros. Idempotent enough to skip when nothing needs resolving."""
    if not (query.participant or query.participant_identifier or query.chatbot):
        return query
    participant_ids = query.participant_ids
    experiment_ids = query.experiment_ids
    is_empty = query.filter_is_empty
    if query.participant or query.participant_identifier:
        # An identifier can match several participants (one per platform), so this resolves to a list.
        lookup = {"public_id": query.participant} if query.participant else {"identifier": query.participant_identifier}
        participant_ids = list(Participant.objects.filter(team=query.team, **lookup).values_list("id", flat=True))
        is_empty = is_empty or not participant_ids
    if query.chatbot:
        # ``get_all()`` so an archived chatbot still resolves to its id (its activity is reached elsewhere
        # by manager-agnostic relation traversal); each version owns its own ``public_id``, so this is at
        # most one id, but keep it a list for a uniform ``__in`` filter.
        experiment_ids = list(
            Experiment.objects.get_all().filter(team=query.team, public_id=query.chatbot).values_list("id", flat=True)
        )
        is_empty = is_empty or not experiment_ids
    return replace(query, participant_ids=participant_ids, experiment_ids=experiment_ids, filter_is_empty=is_empty)


def usage_query(query: UsageQuery) -> UsageResult:
    results = _aggregate(query) if query.granularity == GRANULARITY_TOTAL else _bucketed(query)
    return UsageResult(
        period_start=query.start,
        period_end=query.end,
        timezone=str(query.tz),
        granularity=query.granularity,
        results=results,
    )


def _aggregate(query: UsageQuery) -> dict:
    """One aggregate block per requested metric over the whole ``[start, end)`` window."""
    results: dict = {}
    if METRIC_MESSAGES in query.metrics:
        results[METRIC_MESSAGES] = _message_counts(query)
    if METRIC_SESSIONS in query.metrics:
        results[METRIC_SESSIONS] = _session_count(query)
    if METRIC_PARTICIPANTS in query.metrics:
        results[METRIC_PARTICIPANTS] = _active_participant_count(query)
    if METRIC_COST in query.metrics or METRIC_TOKENS in query.metrics:
        # cost/tokens share the UsageRecord read path, so resolve the participant filter to ids once.
        cost_filter = _cost_filter(query)
        if METRIC_COST in query.metrics:
            results[METRIC_COST] = _cost(query, cost_filter)
        if METRIC_TOKENS in query.metrics:
            results[METRIC_TOKENS] = _token_counts(query, cost_filter)
    return results


def _bucketed(query: UsageQuery) -> list[dict]:
    """One row per time bucket in ``[start, end)``, zero-filled so every bucket in the window appears
    (the max-window guard in the param serializer bounds how many that can be). Each metric runs a
    single grouped query truncated in ``tz``; results are keyed back onto the buckets by local date."""
    trunc = _TRUNC[query.granularity]
    starts = list(_iter_bucket_starts(query.start, query.end, query.granularity, query.tz))
    rows: list[dict] = [{"bucket_start": bucket} for bucket in starts]

    # (grouped-query fn, empty-bucket value factory) per metric. The factory (not a shared value) so
    # each empty bucket gets its own object rather than aliasing one instance.
    metric_specs = (
        (METRIC_MESSAGES, _message_counts_by_bucket, lambda: MessageCounts(human=0, ai=0, total=0)),
        (METRIC_SESSIONS, _session_counts_by_bucket, lambda: 0),
        (METRIC_PARTICIPANTS, _participant_counts_by_bucket, lambda: 0),
    )
    for metric, counts_by_bucket, empty in metric_specs:
        if metric not in query.metrics:
            continue
        by_date = counts_by_bucket(query, trunc)
        for row in rows:
            row[metric] = by_date.get(row["bucket_start"].date(), empty())

    _fill_cost_tokens_buckets(query, rows)
    return rows


def _fill_cost_tokens_buckets(query: UsageQuery, rows: list[dict]) -> None:
    """Add the ``cost``/``tokens`` blocks to each bucket row, if requested. Both come from one
    ``UsageRecord`` timeseries (via the cost read path) so they reconcile with each other and with the
    ``total``-granularity figures; buckets with no records are zero-filled."""
    if METRIC_COST not in query.metrics and METRIC_TOKENS not in query.metrics:
        return
    by_date = _cost_tokens_by_bucket(query)
    for row in rows:
        block = by_date.get(row["bucket_start"].date())
        if METRIC_COST in query.metrics:
            row[METRIC_COST] = block["cost"] if block else CostBlock(total=Decimal(0), currency="USD")
        if METRIC_TOKENS in query.metrics:
            row[METRIC_TOKENS] = block["tokens"] if block else TokenCounts(prompt=0, completion=0, total=0)


def _cost_tokens_by_bucket(query: UsageQuery) -> dict:
    """``{local date: {'cost': CostBlock, 'tokens': TokenCounts}}`` for the window. A participant filter
    that matches nobody yields ``{}`` so every bucket zero-fills, matching ``_cost``/``_token_counts``."""
    cost_filter = _cost_filter(query)
    if cost_filter.is_empty:
        return {}
    rows = reporting.usage_timeseries(
        query.team,
        start=query.start,
        end=query.end,
        granularity=query.granularity,
        tz=query.tz,
        filters=cost_filter.filters,
    )
    return {
        _bucket_date(row["bucket"], query.tz): {
            "cost": CostBlock(total=row["cost"], currency=row["currency"]),
            "tokens": TokenCounts(prompt=row["prompt"], completion=row["completion"], total=row["total"]),
        }
        for row in rows
    }


def _cost(query: UsageQuery, cost_filter: _CostFilter) -> CostBlock:
    """Total priced spend for the window with its currency, from ``UsageRecord`` via the cost read path
    so it reconciles with the dashboard's cost panel. A participant filter that matches nobody zeroes
    the total without touching the DB."""
    if cost_filter.is_empty:
        return CostBlock(total=Decimal(0), currency="USD")
    total = reporting.cost_total(query.team, start=query.start, end=query.end, filters=cost_filter.filters)
    return CostBlock(total=total.total, currency=total.currency)


def _token_counts(query: UsageQuery, cost_filter: _CostFilter) -> TokenCounts:
    """Prompt/completion/total token counts from ``UsageRecord``, split by ``service_kind``. Shares the
    window and filters with ``_cost`` so a client's cost and tokens for one window reconcile."""
    if cost_filter.is_empty:
        return TokenCounts(prompt=0, completion=0, total=0)
    counts = reporting.token_counts(query.team, start=query.start, end=query.end, filters=cost_filter.filters)
    return TokenCounts(prompt=counts.prompt, completion=counts.completion, total=counts.total)


def _cost_filter(query: UsageQuery) -> _CostFilter:
    """Build the ``CostFilters`` the reporting read path honours from the query's already-resolved ids
    (see :func:`resolve_query_filters`). ``is_empty`` carries through ``filter_is_empty`` so the caller
    short-circuits to zeros — ``CostFilters`` treats an empty id list as "no filter", not "match none",
    so an unmatched id filter would otherwise widen the result to everything."""
    kwargs: dict = {}
    if query.participant_ids is not None:
        kwargs["participant_ids"] = query.participant_ids
    if query.experiment_ids is not None:
        kwargs["experiment_ids"] = query.experiment_ids
    if query.platform:
        kwargs["platform_names"] = [query.platform]
    return _CostFilter(filters=reporting.CostFilters(**kwargs), is_empty=query.filter_is_empty)


def _session_queryset(query: UsageQuery) -> QuerySet[ExperimentSession]:
    """Team-scoped ``ExperimentSession`` *started* (``created_at``) within the window. Evaluation-harness
    sessions and sessions still in ``SETUP`` (created but never engaged) are excluded so the count
    reflects real participant usage, matching the dashboard's session definition."""
    queryset = (
        ExperimentSession.objects.filter(team=query.team, created_at__gte=query.start, created_at__lt=query.end)
        .exclude(platform=ChannelPlatform.EVALUATIONS)
        .exclude(status=SessionStatus.SETUP)
    )
    # Filter on the session's own FK columns (resolved ids), so no join to experiment/participant.
    if query.participant_ids is not None:
        queryset = queryset.filter(participant_id__in=query.participant_ids)
    if query.experiment_ids is not None:
        queryset = queryset.filter(experiment_id__in=query.experiment_ids)
    if query.platform:
        queryset = queryset.filter(platform=query.platform)
    return queryset


def _session_count(query: UsageQuery) -> int:
    return _session_queryset(query).count()


def _active_participant_count(query: UsageQuery) -> int:
    """Distinct participants *active* in the window — those with at least one human/AI message in it.
    Keyed off message activity (not session creation) so a participant active in a session started
    earlier is still counted, and restricted to the same human/AI categories the ``messages`` metric
    surfaces so the two metrics agree on who counts as active (internal ``system`` messages excluded)."""
    return _active_participant_queryset(query).aggregate(
        n=Count("chat__experiment_session__participant", distinct=True)
    )["n"]


def _message_counts(query: UsageQuery) -> MessageCounts:
    """Human/AI/total message counts for the window. ``total`` is ``human + ai`` (the two surfaced
    categories); system messages are internal and excluded so the parts always sum to the total."""
    # Filtered Count aggregates return 0 for no matches, never None.
    counts = _message_queryset(query).aggregate(
        human=Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
        ai=Count("id", filter=Q(message_type=ChatMessageType.AI)),
    )
    human = counts["human"]
    ai = counts["ai"]
    return MessageCounts(human=human, ai=ai, total=human + ai)


def _message_queryset(query: UsageQuery) -> QuerySet[ChatMessage]:
    """Team-scoped ``ChatMessage`` in the window. ``ChatMessage`` has no direct team FK, so scope via
    ``chat__team``; the participant lives two relations away, at
    ``chat__experiment_session__participant``. Backed by the ``(chat, message_type, created_at)`` index.
    """
    queryset = ChatMessage.objects.filter(chat__team=query.team, created_at__gte=query.start, created_at__lt=query.end)
    # Filter on the session's FK-id columns (resolved ids), so the message query joins through the chat
    # and session it needs anyway but not the experiment/participant tables.
    if query.participant_ids is not None:
        queryset = queryset.filter(chat__experiment_session__participant_id__in=query.participant_ids)
    if query.experiment_ids is not None:
        queryset = queryset.filter(chat__experiment_session__experiment_id__in=query.experiment_ids)
    if query.platform:
        queryset = queryset.filter(chat__experiment_session__platform=query.platform)
    return queryset


def _active_participant_queryset(query: UsageQuery) -> QuerySet[ChatMessage]:
    return _message_queryset(query).filter(message_type__in=(ChatMessageType.HUMAN, ChatMessageType.AI))


def _message_counts_by_bucket(query: UsageQuery, trunc) -> dict:
    rows = (
        _message_queryset(query)
        .annotate(bucket=trunc("created_at", tzinfo=query.tz))
        .values("bucket")
        .annotate(
            human=Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
            ai=Count("id", filter=Q(message_type=ChatMessageType.AI)),
        )
    )
    return {
        _bucket_date(row["bucket"], query.tz): MessageCounts(
            human=row["human"], ai=row["ai"], total=row["human"] + row["ai"]
        )
        for row in rows
    }


def _session_counts_by_bucket(query: UsageQuery, trunc) -> dict:
    return _scalar_by_bucket(_session_queryset(query), trunc, query.tz, Count("id"))


def _participant_counts_by_bucket(query: UsageQuery, trunc) -> dict:
    return _scalar_by_bucket(
        _active_participant_queryset(query),
        trunc,
        query.tz,
        Count("chat__experiment_session__participant", distinct=True),
    )


def _scalar_by_bucket(queryset: QuerySet, trunc, tz: ZoneInfo, aggregate) -> dict:
    """Group ``queryset`` into ``tz``-truncated buckets and reduce each to a single integer via
    ``aggregate``, keyed by local calendar date. Shared by the session and participant metrics."""
    rows = queryset.annotate(bucket=trunc("created_at", tzinfo=tz)).values("bucket").annotate(n=aggregate)
    return {_bucket_date(row["bucket"], tz): row["n"] for row in rows}


def _iter_bucket_starts(start: datetime, end: datetime, granularity: str, tz: ZoneInfo):
    """Yield the tz-aware bucket-start datetimes covering ``[start, end)``, oldest first. The first
    bucket is ``start`` truncated to the granularity boundary in ``tz`` (so it may precede ``start``
    for an unaligned explicit window); the DB query still only counts activity from ``start`` on."""
    step = _STEP[granularity]
    cursor = _truncate_local(start.astimezone(tz), granularity)
    while cursor < end:
        yield cursor
        cursor = cursor + step


def _truncate_local(dt: datetime, granularity: str) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == GRANULARITY_DAILY:
        return midnight
    if granularity == GRANULARITY_WEEKLY:
        return midnight - timedelta(days=midnight.weekday())  # Monday, matching Django's TruncWeek
    return midnight.replace(day=1)  # monthly


def _bucket_date(value, tz: ZoneInfo):
    """Normalise a DB truncation result to its local calendar date. ``TruncDate`` yields a ``date``
    already; ``TruncWeek``/``TruncMonth`` yield a datetime whose local date is the bucket boundary."""
    if isinstance(value, datetime):
        return value.astimezone(tz).date()
    return value


def _month_bounds(period: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Half-open ``[start, end)`` for a calendar month, computed in ``tz``. Returns tz-aware
    datetimes; Django converts them for the UTC-stored ``created_at`` column. ``relativedelta``
    keeps the same ``tz`` instance, and midnight-on-the-1st stays midnight across a DST transition.
    """
    now_local = timezone.now().astimezone(tz)
    month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == PERIOD_PREVIOUS_MONTH:
        return month_start - relativedelta(months=1), month_start
    return month_start, month_start + relativedelta(months=1)
