"""Build EvaluationMessage rows from chat sessions, one chunk of sessions at a time."""

import itertools
import logging
from collections import defaultdict
from collections.abc import Generator

import dictdiffer
from django.db.models import F

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.models import EvaluationMessage, EvaluationMessageContent
from apps.experiments.filters import ChatMessageFilter
from apps.experiments.models import ExperimentSession
from apps.trace.models import Trace, TraceStatus
from apps.web.dynamic_filters.datastructures import FilterParams

logger = logging.getLogger("ocs.evaluations")

# Sessions loaded per chunk when building session-mode evaluation messages. Mirrors
# EXPORT_CHUNK_SIZE in apps.experiments.export — the same bounded-memory pattern.
SESSION_CHUNK_SIZE = 200


def _latest_participant_data_by_session(session_ids: list[int]) -> dict[int, dict]:
    """Map session id -> participant_data as of each session's last completed turn.

    Queries Trace directly rather than joining through ChatMessage.input_message_trace: that
    is a reverse FK, so joining it emits one row per Trace and duplicates messages. DISTINCT ON
    returns a single row per session, and requiring both a non-PENDING status and an
    output_message skips turns that are still running or never produced a reply. The
    participant_data diff is applied to get the end-of-turn snapshot, matching
    apps.experiments.export._get_participant_data_for_message.
    """
    rows = (
        Trace.objects.filter(session_id__in=session_ids, output_message__isnull=False)
        .exclude(status=TraceStatus.PENDING)
        .order_by("session_id", "-timestamp", "-id")
        .distinct("session_id")
        .values_list("session_id", "participant_data", "participant_data_diff")
    )
    participant_data_by_session = {}
    for session_id, participant_data, participant_data_diff in rows:
        participant_data = participant_data or {}
        if participant_data_diff:
            try:
                participant_data = dictdiffer.patch(participant_data_diff, participant_data)
            except (KeyError, IndexError, TypeError, ValueError):
                # A diff that no longer applies to its snapshot must not abort the whole stream:
                # the caller may be part-way through a multi-thousand-session clone, and every
                # retry would fail on this same session. Fall back to the start-of-turn snapshot.
                logger.warning("Ignoring unapplicable participant_data_diff on latest trace for session %s", session_id)
        participant_data_by_session[session_id] = participant_data
    return participant_data_by_session


def _build_session_evaluation_messages(sessions: list[ExperimentSession]) -> Generator["EvaluationMessage"]:
    """Build one EvaluationMessage per session in *sessions*, using two queries for the batch."""
    session_ids = [session.id for session in sessions]
    history_by_chat: dict[int, list[dict]] = defaultdict(list)
    # Grouped by chat_id (a forward field on ExperimentSession) so no join is needed, and
    # read as values_list rather than model instances: only these three fields reach
    # `history`, and instantiating a ChatMessage per message is what made this unbounded.
    message_rows = (
        ChatMessage.objects.filter(chat_id__in=[session.chat_id for session in sessions])
        .order_by("chat_id", "created_at", "id")
        .values_list("chat_id", "message_type", "content", "summary")
    )
    for chat_id, message_type, content, summary in message_rows:
        history_by_chat[chat_id].append({"message_type": message_type, "content": content, "summary": summary})

    participant_data_by_session = _latest_participant_data_by_session(session_ids)

    for session in sessions:
        history = history_by_chat.get(session.chat_id)
        if not history:
            continue  # a session with no messages produces no EvaluationMessage
        yield EvaluationMessage(
            session=session,
            input={},
            output={},
            history=history,
            participant_data=participant_data_by_session.get(session.id, {}),
            # Sourced from the session, not the trace: Trace.session_state is the snapshot taken
            # when the trace *opened*, while post-turn state is written back to
            # ExperimentSession.state (apps/chat/bots.py). Using the session keeps this
            # end-of-conversation, consistent with the end-of-turn participant_data above and
            # with how exports report session state (apps/experiments/export.py).
            session_state=session.state or {},
            metadata={
                "session_id": str(session.external_id),
                "experiment_id": str(session.experiment.public_id),
                "created_mode": "clone",
            },
            input_chat_message=None,
            expected_output_chat_message=None,
        )


def iter_session_evaluation_messages(
    session_external_ids: list[str], team=None, chunk_size: int | None = None
) -> Generator["EvaluationMessage"]:
    """Yield one EvaluationMessage per session, with the full conversation as history.

    Unlike make_evaluation_messages_from_sessions (which creates one message per human-AI pair),
    this creates a single message per session for holistic session evaluation.

    Sessions are loaded one chunk at a time so memory stays flat regardless of how many were
    selected — only one chunk's messages are resident at a time. Materialising every ChatMessage
    across all sessions at once previously OOM-killed the worker (see #3963).
    """
    if not session_external_ids:
        return

    base_qs = ExperimentSession.objects.filter(external_id__in=session_external_ids)
    if team is not None:
        base_qs = base_qs.filter(team=team)

    yield from iter_session_evaluation_messages_for_sessions(base_qs, chunk_size=chunk_size)


def iter_session_evaluation_messages_for_sessions(
    session_qs, chunk_size: int | None = None
) -> Generator["EvaluationMessage"]:
    """Yield one EvaluationMessage per session in *session_qs*, a chunk of sessions at a time.

    Takes a queryset rather than a list of ids so a caller holding a filter (rather than a
    user-supplied selection) never has to materialise the ids to hand them over.
    """
    # Resolved here rather than as a default argument so the module constant stays patchable
    # in tests, matching apps.experiments.export's use of EXPORT_CHUNK_SIZE.
    chunk_size = chunk_size or SESSION_CHUNK_SIZE

    # Resolve to pks once, then page over that list. Keyset-paginating the source queryset
    # instead would re-run its filter (external_id IN-lists of 10k bind parameters, or a
    # multi-column session filter) on every chunk, and would re-resolve relative date ranges
    # against a moving `now()` mid-walk. A list of ints is cheap to hold (~8 bytes each)
    # compared to the message rows it lets us avoid.
    # ExperimentSession.Meta.ordering is ["-created_at"], so pk ordering must be explicit.
    session_pks = list(session_qs.order_by("pk").values_list("pk", flat=True))

    for pk_chunk in itertools.batched(session_pks, chunk_size, strict=False):
        sessions = list(ExperimentSession.objects.filter(pk__in=pk_chunk).select_related("experiment").order_by("pk"))
        yield from _build_session_evaluation_messages(sessions)


def iter_message_evaluation_messages_for_sessions(
    session_qs,
    *,
    filter_params: FilterParams | None = None,
    timezone: str | None = None,
    chunk_size: int | None = None,
) -> Generator["EvaluationMessage"]:
    """Yield one EvaluationMessage per human-AI pair, one chunk of sessions at a time (memory bounded by the chunk)."""
    chunks = iter_message_evaluation_chunks(
        session_qs, filter_params=filter_params, timezone=timezone, chunk_size=chunk_size
    )
    yield from itertools.chain.from_iterable(messages for _, messages in chunks)


def iter_message_evaluation_chunks(
    session_qs,
    *,
    filter_params: FilterParams | None = None,
    timezone: str | None = None,
    chunk_size: int | None = None,
) -> Generator[tuple[int, list["EvaluationMessage"]]]:
    """Yield (session_count, pair messages) for each chunk of sessions in *session_qs*."""
    # Read at call time so tests can patch SESSION_CHUNK_SIZE.
    chunk_size = chunk_size or SESSION_CHUNK_SIZE

    # Resolved to pks once for the same reasons as iter_session_evaluation_messages_for_sessions:
    # re-running the source filter per chunk would cost a query each time and re-resolve
    # relative date ranges against a moving now().
    session_pks = list(session_qs.order_by("pk").values_list("pk", flat=True))

    for pk_chunk in itertools.batched(session_pks, chunk_size, strict=False):
        messages = ChatMessage.objects.filter(chat__experiment_session__pk__in=pk_chunk)
        if filter_params is not None:
            messages = ChatMessageFilter().apply(messages, filter_params, timezone)
        message_rows = messages.order_by("chat__experiment_session__pk", "created_at").values_list(
            "chat__experiment_session__external_id", "id"
        )
        message_ids_per_session = defaultdict(list)
        for external_id, message_id in message_rows:
            message_ids_per_session[str(external_id)].append(message_id)

        yield len(pk_chunk), make_evaluation_messages_from_sessions(message_ids_per_session)


def make_session_evaluation_messages(session_external_ids: list[str], team=None) -> list["EvaluationMessage"]:
    """Eager wrapper around iter_session_evaluation_messages.

    Only safe for small session counts; large jobs should consume the generator so memory
    stays bounded.
    """
    return list(iter_session_evaluation_messages(session_external_ids, team=team))


def _session_chat_messages(session_id: str) -> list[ChatMessage]:
    """Return every message in the session, oldest first, annotated with trace and experiment data."""
    return list(
        ChatMessage.objects.filter(chat__experiment_session__external_id=session_id)
        .annotate(
            experiment_public_id=F("chat__experiment_session__experiment__public_id"),
            participant_data=F("input_message_trace__participant_data"),
            session_state=F("input_message_trace__session_state"),
        )
        .prefetch_related("comments", "tags", "input_message_trace")
        .order_by("created_at")
    )


def _history_entry(message: ChatMessage) -> dict:
    """Return the history dict for one chat message."""
    return {
        "message_type": message.message_type,
        "content": message.content,
        "summary": getattr(message, "summary", None),
    }


def _add_message_annotations(context: dict, message: ChatMessage) -> None:
    """Append the message's comments and non-system tags to *context*."""
    if comments := list(message.comments.all()):
        context.setdefault("comments", []).extend([comment.comment for comment in comments])
    if tags := list(message.tags.all()):
        context_tags = context.get("tags", [])
        context_tags.extend([tag.name for tag in tags if not tag.is_system_tag])
        context["tags"] = list(dict.fromkeys(context_tags))  # dedupe preserving order


def _message_context(*messages: ChatMessage) -> dict:
    """Build the evaluation context from the first message's timestamp and every message's annotations."""
    context = {"current_datetime": messages[0].created_at.isoformat()}
    for message in messages:
        _add_message_annotations(context, message)
    return context


def _is_target_pair(current: ChatMessage, following: ChatMessage | None, target_ids: set[int]) -> bool:
    """Return whether *current* and *following* form a (HUMAN, AI) pair with at least one targeted message."""
    if following is None:
        return False
    if current.message_type != ChatMessageType.HUMAN or following.message_type != ChatMessageType.AI:
        return False
    return current.id in target_ids or following.id in target_ids


def _shared_attrs(
    session: ExperimentSession | None, session_id: str, current: ChatMessage, history: list[dict]
) -> dict:
    """Return the EvaluationMessage fields common to pair and orphan messages."""
    return {
        "session": session,
        "history": [entry.copy() for entry in history],
        "metadata": {
            "session_id": session_id,
            "experiment_id": str(current.experiment_public_id),
        },
        "participant_data": current.participant_data or {},
        "session_state": current.session_state or {},
    }


def _pair_message(current: ChatMessage, following: ChatMessage, shared: dict) -> EvaluationMessage:
    """Build an EvaluationMessage from a human message and the AI reply that follows it."""
    return EvaluationMessage(
        input_chat_message=current,
        input=EvaluationMessageContent(content=current.content, role="human").model_dump(),
        expected_output_chat_message=following,
        output=EvaluationMessageContent(content=following.content, role="ai").model_dump(),
        context=_message_context(current, following),
        **shared,
    )


def _orphan_message(message: ChatMessage, shared: dict) -> EvaluationMessage:
    """Build an EvaluationMessage from a targeted message that is not part of a (HUMAN, AI) pair."""
    context = _message_context(message)
    if message.message_type == ChatMessageType.HUMAN:
        # A human message with no AI reply, possibly because the AI message failed to generate
        return EvaluationMessage(
            input_chat_message=message,
            input=EvaluationMessageContent(content=message.content, role="human").model_dump(),
            expected_output_chat_message=None,
            output={},
            context=context,
            **shared,
        )
    # An AI message with no human input, possibly because of a scheduled message, AI seed, etc.
    return EvaluationMessage(
        input_chat_message=None,
        input={},
        expected_output_chat_message=message,
        output=EvaluationMessageContent(content=message.content, role="ai").model_dump(),
        context=context,
        **shared,
    )


def _pair_messages_for_session(
    session: ExperimentSession | None, session_id: str, target_ids: set[int]
) -> list[EvaluationMessage]:
    """Build the evaluation messages for one session's targeted messages, in conversation order."""
    messages = _session_chat_messages(session_id)
    evaluation_messages = []
    history: list[dict] = []
    i = 0
    while i < len(messages):
        current = messages[i]
        following = messages[i + 1] if i + 1 < len(messages) else None
        shared = _shared_attrs(session, session_id, current, history)
        if _is_target_pair(current, following, target_ids):
            evaluation_messages.append(_pair_message(current, following, shared))
            history.extend([_history_entry(current), _history_entry(following)])
            i += 2
            continue
        if current.id in target_ids:
            evaluation_messages.append(_orphan_message(current, shared))
        history.append(_history_entry(current))
        i += 1
    return evaluation_messages


def make_evaluation_messages_from_sessions(message_ids_per_session: dict[str, list[str]]) -> list["EvaluationMessage"]:
    """Build one EvaluationMessage per targeted human-AI pair or orphan message, session by session."""
    session_map = {
        str(s.external_id): s for s in ExperimentSession.objects.filter(external_id__in=message_ids_per_session.keys())
    }
    new_messages = []
    for session_id, target_message_ids in message_ids_per_session.items():
        session = session_map.get(str(session_id))
        new_messages.extend(_pair_messages_for_session(session, session_id, set(target_message_ids)))
    return new_messages
