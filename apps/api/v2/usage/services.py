"""Query orchestration for the usage API (``GET /api/v2/usage/``).

This is the single place that turns validated query params into team-scoped aggregates. The first
slice (#3848) supports the ``messages`` metric over a calendar month; later slices add sessions,
participants, cost/tokens, explicit windows, granularity, and grouping without changing this
contract. See ``docs/design/usage-api.md``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Q
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.teams.models import Team

# Metric identifiers. Later slices extend SUPPORTED_METRICS; the param serializer validates against it.
METRIC_MESSAGES = "messages"
SUPPORTED_METRICS = frozenset({METRIC_MESSAGES})

# Calendar-month period selectors.
PERIOD_CURRENT_MONTH = "current_month"
PERIOD_PREVIOUS_MONTH = "previous_month"
PERIOD_CHOICES = (PERIOD_CURRENT_MONTH, PERIOD_PREVIOUS_MONTH)


class MessageCounts(TypedDict):
    human: int
    ai: int
    total: int


@dataclass(frozen=True)
class UsageResult:
    """The endpoint response, pre-serialisation. ``results`` maps each requested metric name to its
    aggregate block. It is a single object (not a list) because this slice is always ungrouped;
    grouping arrives in a later slice."""

    period_start: datetime
    period_end: datetime
    timezone: str
    results: dict


def usage_query(
    team: Team,
    *,
    metrics: set[str],
    period: str,
    tz: ZoneInfo,
    participant: str | None = None,
    participant_identifier: str | None = None,
) -> UsageResult:
    start, end = _month_bounds(period, tz)
    results: dict = {}
    if METRIC_MESSAGES in metrics:
        results[METRIC_MESSAGES] = _message_counts(team, start, end, participant, participant_identifier)
    return UsageResult(period_start=start, period_end=end, timezone=str(tz), results=results)


def _message_counts(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> MessageCounts:
    """Human/AI/total message counts for the window. ``total`` is ``human + ai`` (the two surfaced
    categories); system messages are internal and excluded so the parts always sum to the total.

    ``ChatMessage`` has no direct team FK, so scope via ``chat__team``; the participant lives two
    relations away, at ``chat__experiment_session__participant``. Backed by the
    ``(chat, message_type, created_at)`` index.
    """
    queryset = ChatMessage.objects.filter(chat__team=team, created_at__gte=start, created_at__lt=end)
    if participant:
        queryset = queryset.filter(chat__experiment_session__participant__public_id=participant)
    if participant_identifier:
        queryset = queryset.filter(chat__experiment_session__participant__identifier=participant_identifier)

    # Filtered Count aggregates return 0 for no matches, never None.
    counts = queryset.aggregate(
        human=Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
        ai=Count("id", filter=Q(message_type=ChatMessageType.AI)),
    )
    human = counts["human"]
    ai = counts["ai"]
    return MessageCounts(human=human, ai=ai, total=human + ai)


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
