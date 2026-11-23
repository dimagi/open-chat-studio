"""Activity metrics over sessions, messages, and participants (#3905).

Every function here is the canonical definition per ADR-0051: half-open
`[start, end)` windows, evaluation-harness and `SETUP`-session activity
excluded through the predicates in `filters.py`, conversation turns are human
and AI messages. `sessions_active` (a conversation turn in the window) and
`sessions_started` (created in the window) answer different questions;
`sessions_in_setup` is the complement of `sessions_started` over
non-evaluation sessions created in the window, so setup drop-off stays
countable. Timeseries bucket on the vocabulary in `buckets.py` and are keyed
by tz-aware bucket start; callers zero-fill the gaps from `bucket_starts`.

Filter semantics are `UsageFilters`' own. `include_archived` is not consulted
here: it governs experiment enumeration, which only `filtered_querysets`
returns.
"""

from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

from django.db.models import Count, Exists, OuterRef, Q, QuerySet

from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession
from apps.teams.models import Team

from .buckets import by_bucket
from .filters import (
    AI_AUTHORED,
    CONVERSATION_MESSAGE_TYPES,  # noqa: F401 - re-exported for callers importing it from this module
    HUMAN_AUTHORED,
    UsageFilters,
    activity_sessions,
    chat_tag_exists_pair,
    conversation_messages,
    evaluation_platform,
    exclude_non_activity,
    setup_session,
)


class MessageCounts(TypedDict):
    human: int
    ai: int
    total: int


# Human/AI message-count annotations, shared by the total, bucketed, and grouped
# message reads so the split is defined in one place.
MESSAGE_ANNOTATIONS = {
    "human": Count("id", filter=HUMAN_AUTHORED),
    "ai": Count("id", filter=AI_AUTHORED),
}


def message_counts_from_row(row: dict) -> MessageCounts:
    """Build a :class:`MessageCounts` from a row/aggregate carrying ``human``/``ai`` counts."""
    return MessageCounts(human=row["human"], ai=row["ai"], total=row["human"] + row["ai"])


def conversation_message_total(message_queryset: QuerySet[ChatMessage]) -> int:
    """``human + ai`` count over an already-scoped message queryset."""
    return conversation_messages(message_queryset).count()


def messages_queryset(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> QuerySet[ChatMessage]:
    """Team-scoped ``ChatMessage`` in ``[start, end)`` outside evaluation and ``SETUP`` sessions, every
    message type included (ADR-0051).

    ``ChatMessage`` has no team FK, so scope via ``chat__team``; participant and chatbot filters hit the
    session's FK-id columns, so no join to the experiment or participant tables. Narrowing to
    conversation turns belongs to the metrics that count them, not to the scoping: tag analytics reads
    SYSTEM messages from this universe too."""
    queryset = ChatMessage.objects.filter(chat__team=team, created_at__gte=start, created_at__lt=end)
    return _filtered(
        exclude_non_activity(queryset, "chat__experiment_session__"), team, filters, "chat__experiment_session__"
    )


def active_participants_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ChatMessage]:
    """The message rows behind the active-participants count: HUMAN messages only (ADR-0051)."""
    return messages_queryset(team, start=start, end=end, filters=filters).filter(HUMAN_AUTHORED)


def distinct_active_participants(message_queryset: QuerySet[ChatMessage]) -> int:
    """Distinct participants who authored a HUMAN message in an already-scoped message queryset."""
    return message_queryset.filter(HUMAN_AUTHORED).aggregate(
        n=Count("chat__experiment_session__participant", distinct=True)
    )["n"]


def sessions_active_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ExperimentSession]:
    """Team-scoped sessions with a conversation turn in ``[start, end)``, evaluation and ``SETUP``
    sessions excluded (ADR-0051). ``Exists`` rather than a join, so a session with many turns is one row."""
    turn_in_window = Exists(
        ChatMessage.objects.filter(
            chat=OuterRef("chat"),
            message_type__in=CONVERSATION_MESSAGE_TYPES,
            created_at__gte=start,
            created_at__lt=end,
        )
    )
    sessions = activity_sessions(ExperimentSession.objects.filter(team=team))
    return _filtered(sessions.annotate(_has_turn=turn_in_window).filter(_has_turn=True), team, filters)


def sessions_started_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ExperimentSession]:
    """Team-scoped sessions created in the window that left ``SETUP``, evaluation sessions excluded."""
    return _session_base(team, start=start, end=end, filters=filters).exclude(setup_session())


def sessions_in_setup_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ExperimentSession]:
    """Sessions created in the window that are still in ``SETUP``, the complement of ``sessions_started``."""
    return _session_base(team, start=start, end=end, filters=filters).filter(setup_session())


def _session_base(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> QuerySet[ExperimentSession]:
    """Non-evaluation sessions created in the window, with ``filters`` applied."""
    sessions = ExperimentSession.objects.filter(team=team, created_at__gte=start, created_at__lt=end)
    return _filtered(sessions.exclude(evaluation_platform()), team, filters)


def _filtered(queryset: QuerySet, team: Team, filters: UsageFilters, session_path: str = "") -> QuerySet:
    """Apply ``filters`` to a session queryset, or to a message queryset through ``session_path``."""
    if filters.participant_ids is not None:
        queryset = queryset.filter(**{f"{session_path}participant_id__in": filters.participant_ids})
    if filters.experiment_ids is not None:
        queryset = queryset.filter(**{f"{session_path}experiment_id__in": filters.experiment_ids})
    if filters.platforms is not None:
        queryset = queryset.filter(**{f"{session_path}platform__in": filters.platforms})
    if filters.tag_ids:
        tag_on_chat, tag_on_msg = chat_tag_exists_pair(team, filters.tag_ids, "chat_id")
        queryset = queryset.annotate(_tag_on_chat=tag_on_chat, _tag_on_msg=tag_on_msg).filter(
            Q(_tag_on_chat=True) | Q(_tag_on_msg=True)
        )
    return queryset


def messages(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> MessageCounts:
    """Human/AI/total message counts for the window; ``total`` is ``human + ai``."""
    return message_counts_from_row(
        messages_queryset(team, start=start, end=end, filters=filters).aggregate(**MESSAGE_ANNOTATIONS)
    )


def sessions_started(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> int:
    return sessions_started_queryset(team, start=start, end=end, filters=filters).count()


def sessions_in_setup(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> int:
    return sessions_in_setup_queryset(team, start=start, end=end, filters=filters).count()


def active_participants(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> int:
    """Distinct participants with at least one HUMAN message in the window."""
    return distinct_active_participants(messages_queryset(team, start=start, end=end, filters=filters))


def sessions_active(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> int:
    """Sessions with at least one human or AI message in ``[start, end)``, ``SETUP`` and evaluation
    sessions excluded (ADR-0051)."""
    return sessions_active_queryset(team, start=start, end=end, filters=filters).count()


def sessions_active_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict[datetime, int]:
    """``{bucket start: int}`` of distinct sessions with a conversation turn in each bucket. Bucketed
    on the turn's timestamp, not the session's creation: a session spanning three days is active in
    all three."""
    turns = conversation_messages(messages_queryset(team, start=start, end=end, filters=filters))
    return {
        bucket: row["n"]
        for bucket, row in by_bucket(turns, granularity, tz, n=Count("chat__experiment_session", distinct=True))
    }


def messages_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict[datetime, MessageCounts]:
    """``{bucket start: MessageCounts}`` for the non-empty buckets in ``[start, end)``."""
    return {
        bucket: message_counts_from_row(row)
        for bucket, row in by_bucket(
            messages_queryset(team, start=start, end=end, filters=filters), granularity, tz, **MESSAGE_ANNOTATIONS
        )
    }


def sessions_started_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict[datetime, int]:
    """``{bucket start: int}``, bucketed on session creation."""
    return _scalar_series(sessions_started_queryset(team, start=start, end=end, filters=filters), granularity, tz)


def sessions_in_setup_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict[datetime, int]:
    """``{bucket start: int}``, bucketed on session creation."""
    return _scalar_series(sessions_in_setup_queryset(team, start=start, end=end, filters=filters), granularity, tz)


def active_participants_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict[datetime, int]:
    """``{bucket start: int}`` of distinct HUMAN-message authors per bucket."""
    return {
        bucket: row["n"]
        for bucket, row in by_bucket(
            active_participants_queryset(team, start=start, end=end, filters=filters),
            granularity,
            tz,
            n=Count("chat__experiment_session__participant", distinct=True),
        )
    }


def _scalar_series(queryset: QuerySet, granularity: str, tz: ZoneInfo) -> dict[datetime, int]:
    return {bucket: row["n"] for bucket, row in by_bucket(queryset, granularity, tz, n=Count("id"))}
