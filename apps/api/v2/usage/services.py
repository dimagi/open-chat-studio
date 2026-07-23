"""Query orchestration for the usage API (``GET /api/v2/usage/``).

This is the single place that turns a validated ``UsageQuery`` into team-scoped aggregates. It
supports the ``messages``, ``sessions``, ``participants``, ``cost``, and ``tokens`` metrics over an
explicit ``[start, end)`` window (resolved from ``period`` or ``start``/``end`` by the param
serializer) at ``total``/``daily``/``weekly``/``monthly`` granularity, optionally broken down by a
``group_by`` dimension (participant/chatbot/platform). Ungrouped requests go through
:func:`usage_query`; grouped requests are cursor-paginated by the view over :func:`group_entities`
and :func:`group_rows`. See ``docs/design/usage-api.md``.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TypedDict
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.db.models import Count, F, Q, QuerySet
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

# Dimensions the results may be broken down by. When set, the response is one row per group (or per
# (group, bucket) at a finer granularity), cursor-paginated. v2 vocabulary says ``chatbot``, not
# ``experiment`` (ADR-0023); the underlying model is still ``Experiment``.
GROUP_PARTICIPANT = "participant"
GROUP_CHATBOT = "chatbot"
GROUP_PLATFORM = "platform"
GROUP_BY_CHOICES = (GROUP_PARTICIPANT, GROUP_CHATBOT, GROUP_PLATFORM)

# Ceiling on the rows a single grouped page may materialise (``groups × buckets``); see
# :func:`grouped_page_size_cap`.
MAX_GROUPED_ROWS = 10_000

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


# Zeroed block per metric, for buckets/(group, bucket) cells with no activity. Factories (not shared
# instances) so each empty slot gets its own object rather than aliasing one. Single source so the
# grouped and ungrouped zero-fills can't drift apart.
_EMPTY_METRIC = {
    METRIC_MESSAGES: lambda: MessageCounts(human=0, ai=0, total=0),
    METRIC_SESSIONS: lambda: 0,
    METRIC_PARTICIPANTS: lambda: 0,
    METRIC_COST: lambda: CostBlock(total=Decimal(0), currency="USD"),
    METRIC_TOKENS: lambda: TokenCounts(prompt=0, completion=0, total=0),
}


@dataclass(frozen=True)
class UsageResult:
    """The endpoint response, pre-serialisation.

    ``results`` is either a single object mapping each requested metric name to its aggregate block
    (``granularity=total``), or a list of such objects — one per time bucket, each carrying a
    ``bucket_start`` — when a finer granularity is requested. This is the ungrouped response only;
    grouped requests are paginated by the view and never build a ``UsageResult``."""

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
    group_by: str | None = None
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


@dataclass(frozen=True)
class _GroupSpec:
    """Everything the grouped path needs for one ``group_by`` dimension, in a single record so adding a
    dimension is one registry entry and a missing piece is a construction-time error, not a request-time
    ``KeyError``. The ``*_field`` names are the relation from each source to the dimension: ``ChatMessage``
    reaches it two relations away (via its chat's session); ``ExperimentSession``/``UsageRecord`` hold it
    locally."""

    message_field: str
    session_field: str
    usage_field: str
    # A validated UsageQuery -> the paginatable base queryset of groups active in the window.
    entities: Callable[["UsageQuery"], QuerySet]
    # A paginated item -> the value joining it to its aggregated metrics (entity id, or platform slug).
    # A model instance for participant/chatbot, a ``.values`` dict for platform, so params stay untyped.
    page_key: Callable[..., object]
    # A paginated item -> the group's identity block for the response row.
    identity: Callable[..., object]


def _participant_entities(query: "UsageQuery") -> QuerySet:
    return Participant.objects.filter(team=query.team, id__in=_active_group_ids(query))


def _chatbot_entities(query: "UsageQuery") -> QuerySet:
    # ``get_all()`` so archived chatbots active in the window still appear: the message/session paths
    # reach chatbots by relation traversal (archived-inclusive), so grouping must match them or the
    # breakdown would silently drop archived activity that the ungrouped totals still count.
    return Experiment.objects.get_all().filter(team=query.team, id__in=_active_group_ids(query))


def _platform_entities(query: "UsageQuery") -> QuerySet:
    # Platform has no backing model, so return the distinct slugs present, ordered on the ``platform``
    # alias (which also clears ``ChatMessage``'s default ``created_at`` ordering, keeping ``.distinct()``
    # one row per slug). ``evaluations`` is excluded to match the session metric's exclusion.
    return (
        _message_queryset(query)
        .exclude(chat__experiment_session__platform__isnull=True)
        .exclude(chat__experiment_session__platform="")
        .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
        .values(platform=F("chat__experiment_session__platform"))
        .distinct()
        .order_by("chat__experiment_session__platform")
    )


_GROUP_SPECS = {
    GROUP_PARTICIPANT: _GroupSpec(
        message_field="chat__experiment_session__participant",
        session_field="participant",
        usage_field="participant_id",
        entities=_participant_entities,
        page_key=lambda item: item.id,
        identity=lambda item: {
            "public_id": str(item.public_id),
            "identifier": item.identifier,
            "platform": item.platform,
        },
    ),
    GROUP_CHATBOT: _GroupSpec(
        message_field="chat__experiment_session__experiment",
        session_field="experiment",
        usage_field="experiment_id",
        entities=_chatbot_entities,
        page_key=lambda item: item.id,
        identity=lambda item: {"public_id": str(item.public_id), "name": item.name},
    ),
    GROUP_PLATFORM: _GroupSpec(
        message_field="chat__experiment_session__platform",
        session_field="platform",
        usage_field="session__platform",
        entities=_platform_entities,
        page_key=lambda item: item["platform"],
        identity=lambda item: item["platform"],
    ),
}


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


def group_entities(query: UsageQuery) -> QuerySet:
    """The paginatable base for a grouped request: one entry per group *active in the window* (a group
    with at least one message), so breakdown rows never carry all-zero noise for idle groups. The view
    cursor-paginates this and passes the page to :func:`group_rows`. The per-dimension queryset comes
    from the :class:`_GroupSpec` registry."""
    return _GROUP_SPECS[query.group_by].entities(query)


def group_rows(query: UsageQuery, page: list) -> list[dict]:
    """Serialisable breakdown rows for one page of :func:`group_entities`. At ``total`` granularity each
    group yields one row; at a finer granularity each group is expanded to one flat row per time bucket
    in the window (each carrying ``bucket_start``), so the whole page's rows are ``groups × buckets``.
    Metrics absent for a (group, bucket) are zero-filled."""
    keys = [_page_key(query.group_by, item) for item in page]
    buckets = _group_buckets(query)
    index = _grouped_metric_index(query, keys, buckets)
    rows: list[dict] = []
    for item in page:
        key = _page_key(query.group_by, item)
        identity = _group_identity(query.group_by, item)
        for bucket in buckets:
            row: dict = {query.group_by: identity}
            if bucket is not None:
                row["bucket_start"] = bucket
            row.update(index[(key, _bucket_key(bucket, query.tz))])
            rows.append(row)
    return rows


def _active_group_ids(query: UsageQuery) -> QuerySet:
    """Distinct ids of the ``participant``/``chatbot`` entities with a message in the window."""
    return _message_queryset(query).values_list(_GROUP_SPECS[query.group_by].message_field, flat=True).distinct()


def _page_key(group_by: str, item):
    """The value that joins a paginated group to its aggregated metrics — the entity id for
    ``participant``/``chatbot``, the slug for ``platform`` (a ``.values`` dict)."""
    return _GROUP_SPECS[group_by].page_key(item)


def _group_identity(group_by: str, item):
    """The group's identity block for the response row. Participant rows carry both handles a client
    might hold (``public_id`` and ``identifier``); platform is just its slug."""
    return _GROUP_SPECS[group_by].identity(item)


def _group_buckets(query: UsageQuery) -> list:
    """The bucket dimension of the grouped rows: ``[None]`` at ``total`` (one row per group), else the
    tz-aware bucket-start datetimes covering the window (one row per group per bucket)."""
    if query.granularity == GRANULARITY_TOTAL:
        return [None]
    return list(_iter_bucket_starts(query.start, query.end, query.granularity, query.tz))


def grouped_page_size_cap(query: UsageQuery) -> int:
    """Max groups per page so a grouped page materialises at most :data:`MAX_GROUPED_ROWS` rows. A page
    expands to ``groups × buckets`` rows, and the window guard bounds buckets but not their product with
    ``page_size``; this caps that product so a fine granularity over a wide window can't be combined with
    a large ``page_size`` into a huge single response. Cursor pagination still walks every group."""
    return max(1, MAX_GROUPED_ROWS // len(_group_buckets(query)))


def _bucket_key(bucket: datetime | None, tz: ZoneInfo):
    """The local calendar date keying a bucket in the metric index; ``None`` at ``total`` granularity.
    Delegates to :func:`_bucket_date` so the Python-generated bucket starts and the DB truncation results
    normalise to their local date the same way (they must agree for the index and rows to join)."""
    return None if bucket is None else _bucket_date(bucket, tz)


# Human/AI message-count annotations, shared by the total, bucketed, and grouped message reads so the
# split is defined in one place.
_MESSAGE_ANNOTATIONS = {
    "human": Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
    "ai": Count("id", filter=Q(message_type=ChatMessageType.AI)),
}


def _message_counts_from_row(row: dict) -> MessageCounts:
    """Build a :class:`MessageCounts` from a row/aggregate carrying ``human``/``ai`` counts."""
    return MessageCounts(human=row["human"], ai=row["ai"], total=row["human"] + row["ai"])


def _grouped_rows(queryset: QuerySet, *, group_field: str | None, trunc, tz: ZoneInfo, **annotations):
    """Aggregate ``queryset`` by an optional dimension ``group_field`` and an optional tz time bucket
    (``trunc``), applying ``annotations``. Yields ``(group_key, bucket_key, row)`` where each key is
    ``None`` when that axis isn't requested. The single home of the ``.values(...).annotate(...)`` +
    bucket-date boilerplate, shared by the grouped breakdown and the ungrouped timeseries."""
    value_fields = [group_field] if group_field else []
    if trunc is not None:
        queryset = queryset.annotate(bucket=trunc("created_at", tzinfo=tz))
        value_fields.append("bucket")
    for row in queryset.values(*value_fields).annotate(**annotations):
        group_key = row[group_field] if group_field else None
        bucket_key = _bucket_date(row["bucket"], tz) if trunc is not None else None
        yield group_key, bucket_key, row


def _grouped_metric_index(query: UsageQuery, keys: list, buckets: list) -> dict:
    """``{(group_key, bucket_key): {metric: block}}`` for every (group, bucket) on the page, zero-filled.
    Each metric runs a single query grouped by the dimension (and, at a finer granularity, the time
    bucket) restricted to ``keys``; results are keyed back onto the page's (group, bucket) cells."""
    bucket_keys = [_bucket_key(bucket, query.tz) for bucket in buckets]
    index = {(key, bucket_key): {} for key in keys for bucket_key in bucket_keys}

    for metric, rows in _grouped_non_cost_metric_rows(query, keys):
        for group_key, bucket_key, value in rows:
            index[(group_key, bucket_key)][metric] = value
    if METRIC_COST in query.metrics or METRIC_TOKENS in query.metrics:
        _fill_grouped_cost_tokens(query, keys, index)

    _zero_fill_grouped(query, index)
    return index


def _grouped_non_cost_metric_rows(query: UsageQuery, keys: list) -> list:
    """``(metric, (group_key, bucket_key, value) iterator)`` for each requested non-cost metric, built
    conditionally so a metric's query only runs when asked for. Cost/tokens share one UsageRecord read
    and are filled separately by the caller."""
    trunc = None if query.granularity == GRANULARITY_TOTAL else _TRUNC[query.granularity]
    spec = _GROUP_SPECS[query.group_by]

    def by_group(queryset, group_field, **annotations):
        return _grouped_rows(
            queryset.filter(**{f"{group_field}__in": keys}),
            group_field=group_field,
            trunc=trunc,
            tz=query.tz,
            **annotations,
        )

    metric_rows = []
    if METRIC_MESSAGES in query.metrics:
        rows = by_group(_message_queryset(query), spec.message_field, **_MESSAGE_ANNOTATIONS)
        metric_rows.append((METRIC_MESSAGES, ((gk, bk, _message_counts_from_row(row)) for gk, bk, row in rows)))
    if METRIC_SESSIONS in query.metrics:
        rows = by_group(_session_queryset(query), spec.session_field, n=Count("id"))
        metric_rows.append((METRIC_SESSIONS, ((gk, bk, row["n"]) for gk, bk, row in rows)))
    if METRIC_PARTICIPANTS in query.metrics:
        # group_by=participant is rejected upstream, so this only runs for chatbot/platform.
        rows = by_group(
            _active_participant_queryset(query),
            spec.message_field,
            n=Count("chat__experiment_session__participant", distinct=True),
        )
        metric_rows.append((METRIC_PARTICIPANTS, ((gk, bk, row["n"]) for gk, bk, row in rows)))
    return metric_rows


def _fill_grouped_cost_tokens(query: UsageQuery, keys: list, index: dict) -> None:
    """Add the ``cost``/``tokens`` blocks to the (group, bucket) cells from one grouped ``UsageRecord``
    read (via the cost read path) so cost and tokens reconcile with each other and with the totals."""
    cost_filter = _cost_filter(query)
    granularity = None if query.granularity == GRANULARITY_TOTAL else query.granularity
    rows = (
        []
        if cost_filter.is_empty
        else reporting.usage_by_group(
            query.team,
            start=query.start,
            end=query.end,
            breakdown=reporting.GroupBreakdown(
                field=_GROUP_SPECS[query.group_by].usage_field,
                keys=keys,
                granularity=granularity,
                tz=query.tz,
            ),
            # Currency is only read for the cost block, so a tokens-only request skips the extra scan.
            resolve_currency=METRIC_COST in query.metrics,
            filters=cost_filter.filters,
        )
    )
    for row in rows:
        bucket_key = None if granularity is None else _bucket_date(row["bucket"], query.tz)
        cell = index.get((row["key"], bucket_key))
        if cell is None:
            continue
        if METRIC_COST in query.metrics:
            cell[METRIC_COST] = CostBlock(total=row["cost"], currency=row["currency"])
        if METRIC_TOKENS in query.metrics:
            cell[METRIC_TOKENS] = TokenCounts(prompt=row["prompt"], completion=row["completion"], total=row["total"])


def _zero_fill_grouped(query: UsageQuery, index: dict) -> None:
    """Give every (group, bucket) cell a zeroed block for each requested metric it is missing, so the
    response is dense over the page even where a group had no activity in a bucket."""
    for cell in index.values():
        for metric in query.metrics:
            cell.setdefault(metric, _EMPTY_METRIC[metric]())


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

    metric_specs = (
        (METRIC_MESSAGES, _message_counts_by_bucket),
        (METRIC_SESSIONS, _session_counts_by_bucket),
        (METRIC_PARTICIPANTS, _participant_counts_by_bucket),
    )
    for metric, counts_by_bucket in metric_specs:
        if metric not in query.metrics:
            continue
        by_date = counts_by_bucket(query, trunc)
        for row in rows:
            # _EMPTY_METRIC factory (not a shared value) so each empty bucket gets its own object.
            row[metric] = by_date.get(row["bucket_start"].date(), _EMPTY_METRIC[metric]())

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
            row[METRIC_COST] = block["cost"] if block else _EMPTY_METRIC[METRIC_COST]()
        if METRIC_TOKENS in query.metrics:
            row[METRIC_TOKENS] = block["tokens"] if block else _EMPTY_METRIC[METRIC_TOKENS]()


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
    return _message_counts_from_row(_message_queryset(query).aggregate(**_MESSAGE_ANNOTATIONS))


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
    return {
        bucket_key: _message_counts_from_row(row)
        for _, bucket_key, row in _grouped_rows(
            _message_queryset(query), group_field=None, trunc=trunc, tz=query.tz, **_MESSAGE_ANNOTATIONS
        )
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
    return {
        bucket_key: row["n"]
        for _, bucket_key, row in _grouped_rows(queryset, group_field=None, trunc=trunc, tz=tz, n=aggregate)
    }


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
