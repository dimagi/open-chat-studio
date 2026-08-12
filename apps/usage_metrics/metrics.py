"""Activity metrics over sessions, messages, and participants (#3905).

Every function here is the canonical definition per ADR-0051: half-open
`[start, end)` windows, evaluation-harness and `SETUP`-session activity
excluded, conversation turns are human/AI messages (`system` excluded).
`sessions_active` and
`sessions_started` legitimately differ from each other - they answer
different questions, not the same one two ways. `sessions_active` is a
session with a conversation turn in the window (SETUP excluded);
`sessions_started` is a session created in the window (also SETUP-excluded,
via a different mechanism - see `_session_base`). `sessions_in_setup` is the
complement: sessions created in the window still in SETUP, so
`sessions_started + sessions_in_setup` is every non-evaluation session
created in the window.

Filter semantics (see `UsageFilters`): `experiment_ids`/`participant_ids`
distinguish `None` (no filter) from `[]` (matched nobody -> empty result) on
every function in this module. `sessions_active` delegates to
`filtered_querysets`, which treats an empty list as "no filter" (dashboard
truthiness semantics), so it answers the empty-list case itself before
delegating - see its docstring. `tag_ids` narrows to
conversations whose chat or any message carries the tag, and an empty list
always means "no filter" on every function in this module, `sessions_active`
included. `include_archived` is not consulted by any function here: it governs
experiment *enumeration*, which only `filtered_querysets` returns, and activity
metrics count archived-chatbot activity either way (ADR-0051). `sessions_active`
delegates to `filtered_querysets` but reads only its `sessions` queryset, so it
does not forward the field.
"""

from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, QuerySet
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.teams.models import Team

from .dashboard_querysets import filtered_querysets
from .filters import (
    CONVERSATION_MESSAGE_TYPES,  # noqa: F401 - re-exported for callers importing it from this module
    HUMAN_AUTHORED,
    UsageFilters,
    chat_tag_exists_pair,
    conversation_messages,
)

# DB truncation per bucketed granularity (Django's TruncWeek starts weeks on
# Monday). The one home of the granularity vocabulary - the v2 usage API's
# grouped reads import it rather than keeping a copy.
GRANULARITY_TRUNC = {
    "daily": TruncDate,
    "weekly": TruncWeek,
    "monthly": TruncMonth,
}


class MessageCounts(TypedDict):
    human: int
    ai: int
    total: int


# Human/AI message-count annotations, shared by the total, bucketed, and grouped
# message reads so the split is defined in one place.
MESSAGE_ANNOTATIONS = {
    "human": Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
    "ai": Count("id", filter=Q(message_type=ChatMessageType.AI)),
}


def message_counts_from_row(row: dict) -> MessageCounts:
    """Build a :class:`MessageCounts` from a row/aggregate carrying ``human``/``ai`` counts."""
    return MessageCounts(human=row["human"], ai=row["ai"], total=row["human"] + row["ai"])


def conversation_message_total(message_queryset: QuerySet[ChatMessage]) -> int:
    """``human + ai`` count over an already-scoped message queryset."""
    return conversation_messages(message_queryset).count()


def messages_queryset(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> QuerySet[ChatMessage]:
    """Team-scoped ``ChatMessage`` in ``[start, end)``, with evaluation-harness
    activity and ``SETUP``-session activity excluded (ADR-0051). ``SETUP`` is
    excluded on the same universe as ``sessions_active`` drops the session, so
    a ratio built from a count here over a session count stays on one universe.
    ``ChatMessage`` has no direct team FK, so scope via ``chat__team``;
    participant/chatbot filters hit the session's FK-id columns so no join to
    the experiment/participant tables. Backed by the
    ``(chat, message_type, created_at)`` index. Every message *type* is still in
    this universe - the human/AI narrowing belongs to the metrics that count
    conversation turns, not to the scoping.

    Both exclusions cross the nullable session relation, so a message whose
    chat has no session stays in the universe. That is the dashboard's
    long-standing behaviour and is deliberate.
    """
    queryset = (
        ChatMessage.objects.filter(chat__team=team, created_at__gte=start, created_at__lt=end)
        .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
        .exclude(chat__experiment_session__status=SessionStatus.SETUP)
    )
    if filters.participant_ids is not None:
        queryset = queryset.filter(chat__experiment_session__participant_id__in=filters.participant_ids)
    if filters.experiment_ids is not None:
        queryset = queryset.filter(chat__experiment_session__experiment_id__in=filters.experiment_ids)
    if filters.platform:
        queryset = queryset.filter(chat__experiment_session__platform=filters.platform)
    if filters.tag_ids:
        tag_on_chat, tag_on_msg = chat_tag_exists_pair(team, filters.tag_ids, "chat_id")
        queryset = queryset.annotate(_tag_on_chat=tag_on_chat, _tag_on_msg=tag_on_msg).filter(
            Q(_tag_on_chat=True) | Q(_tag_on_msg=True)
        )
    return queryset


def active_participants_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ChatMessage]:
    """The message rows behind the active-participants count: HUMAN messages
    only. Receiving AI output is not activity (ADR-0051)."""
    return messages_queryset(team, start=start, end=end, filters=filters).filter(HUMAN_AUTHORED)


def distinct_active_participants(message_queryset: QuerySet[ChatMessage]) -> int:
    """Distinct participants who authored a HUMAN message in an already-scoped
    message queryset. The one definition of the count, callable from either
    surface's starting queryset."""
    return message_queryset.filter(HUMAN_AUTHORED).aggregate(
        n=Count("chat__experiment_session__participant", distinct=True)
    )["n"]


def sessions_started_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ExperimentSession]:
    """Team-scoped ``ExperimentSession`` *started* (``created_at``) within the
    window. Evaluation-harness sessions and sessions still in ``SETUP``
    (created but never engaged) are excluded so the count reflects real
    participant usage."""
    return _session_base(team, start=start, end=end, filters=filters).exclude(status=SessionStatus.SETUP)


def sessions_in_setup_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ExperimentSession]:
    """Sessions created in the window that are still in ``SETUP`` - the
    complement of ``sessions_started`` over non-evaluation sessions created in
    the window, keeping setup drop-off countable."""
    return _session_base(team, start=start, end=end, filters=filters).filter(status=SessionStatus.SETUP)


def _session_base(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> QuerySet[ExperimentSession]:
    """Shared base for ``sessions_started_queryset``/``sessions_in_setup_queryset``.
    Evaluation sessions are excluded on the session's own ``platform`` column,
    the same column ``sessions_active`` now excludes on (see
    ``dashboard_querysets.py``; ADR-0051)."""
    queryset = ExperimentSession.objects.filter(team=team, created_at__gte=start, created_at__lt=end).exclude(
        platform=ChannelPlatform.EVALUATIONS
    )
    if filters.participant_ids is not None:
        queryset = queryset.filter(participant_id__in=filters.participant_ids)
    if filters.experiment_ids is not None:
        queryset = queryset.filter(experiment_id__in=filters.experiment_ids)
    if filters.platform:
        queryset = queryset.filter(platform=filters.platform)
    if filters.tag_ids:
        tag_on_chat, tag_on_msg = chat_tag_exists_pair(team, filters.tag_ids, "chat_id")
        queryset = queryset.annotate(_tag_on_chat=tag_on_chat, _tag_on_msg=tag_on_msg).filter(
            Q(_tag_on_chat=True) | Q(_tag_on_msg=True)
        )
    return queryset


def messages(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> MessageCounts:
    """Human/AI/total message counts for the window. ``total`` is
    ``human + ai``; system messages are internal and excluded from it."""
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
    """Sessions with at least one human or AI message in ``[start, end)``,
    ``SETUP`` and evaluation sessions excluded (ADR-0051). Computed through
    ``filtered_querysets`` so the dashboard's charts, which read those
    querysets directly, count the same sessions this returns.

    ``filtered_querysets`` treats an empty ``experiment_ids``/``participant_ids``
    list as "no filter" (dashboard truthiness semantics), so the empty-list
    case is answered here before delegating: ``[]`` means "requested but
    matched nobody", the same as every other function in this module."""
    if filters.experiment_ids == [] or filters.participant_ids == []:
        return 0
    querysets = filtered_querysets(
        team,
        start_date=start,
        end_date=end,
        experiment_ids=filters.experiment_ids,
        participant_ids=filters.participant_ids,
        platform_names=[filters.platform] if filters.platform else None,
        tag_ids=filters.tag_ids,
    )
    return querysets["sessions"].count()


def sessions_active_queryset(
    team: Team, *, start: datetime, end: datetime, filters: UsageFilters
) -> QuerySet[ChatMessage]:
    """The conversation-turn message rows behind ``sessions_active``.
    ``messages_queryset`` already drops ``SETUP``-session turns, so these rows
    agree with the scalar count without a second exclusion here."""
    return conversation_messages(messages_queryset(team, start=start, end=end, filters=filters))


def sessions_active_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict:
    """``{local bucket date: int}`` of distinct sessions with a conversation
    turn in each bucket. Bucketed on the message's timestamp, not the session's
    creation - a session spanning three days is active in all three."""
    return {
        bucket: row["n"]
        for bucket, row in _by_bucket(
            sessions_active_queryset(team, start=start, end=end, filters=filters),
            granularity,
            tz,
            n=Count("chat__experiment_session", distinct=True),
        )
    }


def messages_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict:
    """``{local bucket date: MessageCounts}`` for non-empty buckets in
    ``[start, end)``; callers zero-fill the gaps."""
    return {
        bucket: message_counts_from_row(row)
        for bucket, row in _by_bucket(
            messages_queryset(team, start=start, end=end, filters=filters), granularity, tz, **MESSAGE_ANNOTATIONS
        )
    }


def sessions_started_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict:
    """``{local bucket date: int}``, bucketed on session creation."""
    return _scalar_series(sessions_started_queryset(team, start=start, end=end, filters=filters), granularity, tz)


def sessions_in_setup_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict:
    """``{local bucket date: int}``, bucketed on session creation."""
    return _scalar_series(sessions_in_setup_queryset(team, start=start, end=end, filters=filters), granularity, tz)


def active_participants_timeseries(
    team: Team, *, start: datetime, end: datetime, granularity: str, tz: ZoneInfo, filters: UsageFilters
) -> dict:
    """``{local bucket date: int}`` of distinct HUMAN-message authors per bucket."""
    return {
        bucket: row["n"]
        for bucket, row in _by_bucket(
            active_participants_queryset(team, start=start, end=end, filters=filters),
            granularity,
            tz,
            n=Count("chat__experiment_session__participant", distinct=True),
        )
    }


def _scalar_series(queryset: QuerySet, granularity: str, tz: ZoneInfo) -> dict:
    return {bucket: row["n"] for bucket, row in _by_bucket(queryset, granularity, tz, n=Count("id"))}


def _by_bucket(queryset: QuerySet, granularity: str, tz: ZoneInfo, **annotations):
    """Group ``queryset`` into tz-truncated ``created_at`` buckets, applying
    ``annotations``; yields ``(local bucket date, row)``. ``TruncDate`` yields
    a date already; ``TruncWeek``/``TruncMonth`` yield a datetime whose local
    date is the bucket boundary."""
    trunc = GRANULARITY_TRUNC.get(granularity, TruncDate)
    rows = queryset.annotate(bucket=trunc("created_at", tzinfo=tz)).values("bucket").annotate(**annotations)
    for row in rows:
        yield bucket_date(row["bucket"], tz), row


def bucket_date(value, tz: ZoneInfo):
    """Normalise a DB truncation result to its local calendar date.
    ``TruncDate`` yields a ``date`` already; ``TruncWeek``/``TruncMonth``
    yield a datetime whose local date is the bucket boundary."""
    if isinstance(value, datetime):
        return value.astimezone(tz).date()
    return value
