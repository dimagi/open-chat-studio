"""Query orchestration for the usage API (``GET /api/v2/usage/``).

This is the single place that turns validated query params into team-scoped aggregates. It supports
the ``messages``, ``sessions``, and ``participants`` metrics over a calendar month; later slices add
cost/tokens, explicit windows, granularity, and grouping without changing this contract. See
``docs/design/usage-api.md``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Q, QuerySet
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
    if METRIC_SESSIONS in metrics:
        results[METRIC_SESSIONS] = _session_count(team, start, end, participant, participant_identifier)
    if METRIC_PARTICIPANTS in metrics:
        results[METRIC_PARTICIPANTS] = _active_participant_count(team, start, end, participant, participant_identifier)
    return UsageResult(period_start=start, period_end=end, timezone=str(tz), results=results)


def _session_count(
    team: Team,
    start: datetime,
    end: datetime,
    participant: str | None,
    participant_identifier: str | None,
) -> int:
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
    return queryset.count()


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
    queryset = _message_queryset(team, start, end, participant, participant_identifier).filter(
        message_type__in=(ChatMessageType.HUMAN, ChatMessageType.AI)
    )
    return queryset.aggregate(n=Count("chat__experiment_session__participant", distinct=True))["n"]


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
