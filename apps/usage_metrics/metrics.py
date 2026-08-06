"""Activity metrics over sessions, messages, and participants (#3905).

Each function reproduces, unchanged, the semantics its consumer computes
today - this PR extracts, it does not converge. `messages`,
`sessions_started`, and `active_participants` (and their timeseries) are the
v2 usage API's current reads: half-open `[start, end)` windows, evaluation
messages included in `messages`, `total = human + ai`. `sessions_active` is
the dashboard's current definition (any message in the closed window) via
`dashboard_querysets`. `sessions_in_setup` is new and has no consumer yet:
sessions created in the window still in SETUP, so
`sessions_started + sessions_in_setup` is every non-evaluation session
created in the window. The definition-switch PR converges the dashboard and
API semantics as one diff against this module; it also adds the
`sessions_active` timeseries, whose per-period definition is contested today
(the dashboard has two disagreeing chart implementations).

Filter semantics (see `UsageFilters`): for the API-derived reads - `messages`,
`sessions_started`, `sessions_in_setup`, `active_participants`, and their
timeseries - `experiment_ids`/`participant_ids` distinguish `None` (no
filter) from `[]` (matched nobody -> empty result). `sessions_active` does
NOT follow this rule: it delegates to `filtered_querysets`, which treats an
empty `experiment_ids`/`participant_ids` list as "no filter" (dashboard
truthiness semantics), so identical empty-list filters yield opposite
results on the two functions - see its docstring. `tag_ids` narrows to
conversations whose chat or any message carries the tag, and an empty list
always means "no filter" on every function in this module, `sessions_active`
included. `include_archived` is not consulted here - activity metrics count
archived-experiment activity on both surfaces today.
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
from .filters import UsageFilters, chat_tag_exists_pair

_TRUNC = {
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


def messages_queryset(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> QuerySet[ChatMessage]:
    """Team-scoped ``ChatMessage`` in ``[start, end)``, every message type and
    every session kind (evaluation sessions included). ``ChatMessage`` has no
    direct team FK, so scope via ``chat__team``; participant/chatbot filters
    hit the session's FK-id columns so no join to the experiment/participant
    tables. Backed by the ``(chat, message_type, created_at)`` index.
    """
    queryset = ChatMessage.objects.filter(chat__team=team, created_at__gte=start, created_at__lt=end)
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
    """The message rows behind the active-participants count: human/AI only,
    the same categories the ``messages`` metric surfaces."""
    return messages_queryset(team, start=start, end=end, filters=filters).filter(
        message_type__in=(ChatMessageType.HUMAN, ChatMessageType.AI)
    )


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
    Evaluation sessions are excluded on the session's own ``platform`` column.
    This differs from ``sessions_active``, which excludes via
    ``experiment_channel__platform`` (see ``dashboard_querysets.py``);
    ``ExperimentSession.platform`` is an independent nullable ``CharField``,
    so the two exclusions can disagree on a session where the two columns are
    out of sync."""
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
    """Distinct participants with at least one human/AI message in the window."""
    return active_participants_queryset(team, start=start, end=end, filters=filters).aggregate(
        n=Count("chat__experiment_session__participant", distinct=True)
    )["n"]


def sessions_active(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> int:
    """Sessions with at least one message of any type in the CLOSED interval
    ``[start, end]`` - the dashboard's current definition, reproduced
    unchanged (SETUP sessions count; evaluation-channel sessions do not,
    excluded via ``experiment_channel__platform`` rather than the session's
    own ``platform`` column that ``sessions_started``/``sessions_in_setup``
    key on - see ``_session_base``; ``include_archived`` is not consulted).
    The definition-switch PR moves this to a half-open window over human/AI
    messages with SETUP excluded; until then this and the API-derived
    metrics deliberately disagree.

    Empty-list filter semantics also differ from the rest of this module: via
    ``filtered_querysets``, an empty ``experiment_ids`` or ``participant_ids``
    list means "no filter" here (dashboard truthiness semantics), not
    "matched nobody" as it does on ``sessions_started`` and the other
    API-derived reads."""
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
    """``{local bucket date: int}`` of distinct human/AI-message authors per bucket."""
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
    trunc = _TRUNC.get(granularity, TruncDate)
    rows = queryset.annotate(bucket=trunc("created_at", tzinfo=tz)).values("bucket").annotate(**annotations)
    for row in rows:
        yield _bucket_date(row["bucket"], tz), row


def _bucket_date(value, tz: ZoneInfo):
    if isinstance(value, datetime):
        return value.astimezone(tz).date()
    return value
