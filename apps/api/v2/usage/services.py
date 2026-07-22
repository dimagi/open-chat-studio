"""Query orchestration for the usage API (``GET /api/v2/usage/``).

This is the single place that turns validated query params into team-scoped aggregates. It supports
the ``messages``, ``sessions``, and ``participants`` metrics over an explicit ``[start, end)`` window
(resolved from ``period`` or ``start``/``end`` by the param serializer) at ``total``/``daily``/
``weekly``/``monthly`` granularity; later slices add cost/tokens and grouping without changing this
contract. See ``docs/design/usage-api.md``.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Q, QuerySet
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.teams.models import Team

# Metric identifiers. Later slices extend SUPPORTED_METRICS; the param serializer validates against it.
METRIC_MESSAGES = "messages"
METRIC_SESSIONS = "sessions"
METRIC_PARTICIPANTS = "participants"
SUPPORTED_METRICS = frozenset({METRIC_MESSAGES, METRIC_SESSIONS, METRIC_PARTICIPANTS})

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


def usage_query(
    team: Team,
    *,
    metrics: set[str],
    start: datetime,
    end: datetime,
    granularity: str = GRANULARITY_TOTAL,
    tz: ZoneInfo,
    participant: str | None = None,
    participant_identifier: str | None = None,
) -> UsageResult:
    if granularity == GRANULARITY_TOTAL:
        results: dict | list = _aggregate(team, metrics, start, end, participant, participant_identifier)
    else:
        results = _bucketed(team, metrics, start, end, granularity, tz, participant, participant_identifier)
    return UsageResult(period_start=start, period_end=end, timezone=str(tz), granularity=granularity, results=results)


def _aggregate(
    team: Team,
    metrics: set[str],
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> dict:
    """One aggregate block per requested metric over the whole ``[start, end)`` window."""
    results: dict = {}
    if METRIC_MESSAGES in metrics:
        results[METRIC_MESSAGES] = _message_counts(team, start, end, participant, participant_identifier)
    if METRIC_SESSIONS in metrics:
        results[METRIC_SESSIONS] = _session_count(team, start, end, participant, participant_identifier)
    if METRIC_PARTICIPANTS in metrics:
        results[METRIC_PARTICIPANTS] = _active_participant_count(team, start, end, participant, participant_identifier)
    return results


def _bucketed(
    team: Team,
    metrics: set[str],
    start: datetime,
    end: datetime,
    granularity: str,
    tz: ZoneInfo,
    participant: str | None,
    participant_identifier: str | None,
) -> list[dict]:
    """One row per time bucket in ``[start, end)``, zero-filled so every bucket in the window appears
    (the max-window guard in the param serializer bounds how many that can be). Each metric runs a
    single grouped query truncated in ``tz``; results are keyed back onto the buckets by local date."""
    trunc = _TRUNC[granularity]
    starts = list(_iter_bucket_starts(start, end, granularity, tz))
    rows: list[dict] = [{"bucket_start": bucket} for bucket in starts]

    # (grouped-query fn, empty-bucket value factory) per metric. The factory (not a shared value) so
    # each empty bucket gets its own object rather than aliasing one instance.
    metric_specs = (
        (METRIC_MESSAGES, _message_counts_by_bucket, lambda: MessageCounts(human=0, ai=0, total=0)),
        (METRIC_SESSIONS, _session_counts_by_bucket, lambda: 0),
        (METRIC_PARTICIPANTS, _participant_counts_by_bucket, lambda: 0),
    )
    for metric, counts_by_bucket, empty in metric_specs:
        if metric not in metrics:
            continue
        by_date = counts_by_bucket(team, start, end, trunc, tz, participant, participant_identifier)
        for row in rows:
            row[metric] = by_date.get(row["bucket_start"].date(), empty())
    return rows


def _session_queryset(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> QuerySet[ExperimentSession]:
    """Team-scoped ``ExperimentSession`` *started* (``created_at``) within the window. Evaluation-harness
    sessions and sessions still in ``SETUP`` (created but never engaged) are excluded so the count
    reflects real participant usage, matching the dashboard's session definition."""
    queryset = (
        ExperimentSession.objects.filter(team=team, created_at__gte=start, created_at__lt=end)
        .exclude(platform=ChannelPlatform.EVALUATIONS)
        .exclude(status=SessionStatus.SETUP)
    )
    if participant:
        queryset = queryset.filter(participant__public_id=participant)
    if participant_identifier:
        queryset = queryset.filter(participant__identifier=participant_identifier)
    return queryset


def _session_count(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> int:
    return _session_queryset(team, start, end, participant, participant_identifier).count()


def _active_participant_count(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> int:
    """Distinct participants *active* in the window — those with at least one human/AI message in it.
    Keyed off message activity (not session creation) so a participant active in a session started
    earlier is still counted, and restricted to the same human/AI categories the ``messages`` metric
    surfaces so the two metrics agree on who counts as active (internal ``system`` messages excluded)."""
    return _active_participant_queryset(team, start, end, participant, participant_identifier).aggregate(
        n=Count("chat__experiment_session__participant", distinct=True)
    )["n"]


def _message_counts(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> MessageCounts:
    """Human/AI/total message counts for the window. ``total`` is ``human + ai`` (the two surfaced
    categories); system messages are internal and excluded so the parts always sum to the total."""
    # Filtered Count aggregates return 0 for no matches, never None.
    counts = _message_queryset(team, start, end, participant, participant_identifier).aggregate(
        human=Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
        ai=Count("id", filter=Q(message_type=ChatMessageType.AI)),
    )
    human = counts["human"]
    ai = counts["ai"]
    return MessageCounts(human=human, ai=ai, total=human + ai)


def _message_queryset(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> QuerySet[ChatMessage]:
    """Team-scoped ``ChatMessage`` in the window. ``ChatMessage`` has no direct team FK, so scope via
    ``chat__team``; the participant lives two relations away, at
    ``chat__experiment_session__participant``. Backed by the ``(chat, message_type, created_at)`` index.
    """
    queryset = ChatMessage.objects.filter(chat__team=team, created_at__gte=start, created_at__lt=end)
    if participant:
        queryset = queryset.filter(chat__experiment_session__participant__public_id=participant)
    if participant_identifier:
        queryset = queryset.filter(chat__experiment_session__participant__identifier=participant_identifier)
    return queryset


def _active_participant_queryset(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> QuerySet[ChatMessage]:
    return _message_queryset(team, start, end, participant, participant_identifier).filter(
        message_type__in=(ChatMessageType.HUMAN, ChatMessageType.AI)
    )


def _message_counts_by_bucket(
    team: Team,
    start: datetime,
    end: datetime,
    trunc,
    tz: ZoneInfo,
    participant: str | None,
    participant_identifier: str | None,
) -> dict:
    rows = (
        _message_queryset(team, start, end, participant, participant_identifier)
        .annotate(bucket=trunc("created_at", tzinfo=tz))
        .values("bucket")
        .annotate(
            human=Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
            ai=Count("id", filter=Q(message_type=ChatMessageType.AI)),
        )
    )
    return {
        _bucket_date(row["bucket"], tz): MessageCounts(human=row["human"], ai=row["ai"], total=row["human"] + row["ai"])
        for row in rows
    }


def _session_counts_by_bucket(
    team: Team,
    start: datetime,
    end: datetime,
    trunc,
    tz: ZoneInfo,
    participant: str | None,
    participant_identifier: str | None,
) -> dict:
    rows = (
        _session_queryset(team, start, end, participant, participant_identifier)
        .annotate(bucket=trunc("created_at", tzinfo=tz))
        .values("bucket")
        .annotate(n=Count("id"))
    )
    return {_bucket_date(row["bucket"], tz): row["n"] for row in rows}


def _participant_counts_by_bucket(
    team: Team,
    start: datetime,
    end: datetime,
    trunc,
    tz: ZoneInfo,
    participant: str | None,
    participant_identifier: str | None,
) -> dict:
    rows = (
        _active_participant_queryset(team, start, end, participant, participant_identifier)
        .annotate(bucket=trunc("created_at", tzinfo=tz))
        .values("bucket")
        .annotate(n=Count("chat__experiment_session__participant", distinct=True))
    )
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
