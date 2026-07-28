import inspect
import itertools
import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, cast

import dictdiffer
from django.db.models import F
from pydantic import BaseModel, Field, create_model

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.field_definitions import FieldDefinition
from apps.experiments.models import ExperimentSession
from apps.trace.models import Trace, TraceStatus
from apps.utils.fields import sanitize_json_data as fields_sanitize_json_data

logger = logging.getLogger("ocs.evaluations")

# Sessions loaded per chunk when building session-mode evaluation messages. Mirrors
# EXPORT_CHUNK_SIZE in apps.experiments.export — the same bounded-memory pattern.
SESSION_CHUNK_SIZE = 200

if TYPE_CHECKING:
    from apps.evaluations.models import EvaluationMessage


def sanitize_json_data(data: Any) -> Any:
    """
    Recursively sanitize JSON data by removing null bytes and control characters.

    PostgreSQL's JSONB type cannot store null bytes (\u0000) and some control characters
    in text values. This function removes these characters from strings throughout the
    JSON structure.

    Args:
        data: The data to sanitize (dict, list, str, or primitive)

    Returns:
        Sanitized copy of the data
    """
    # Kept as a backwards-compatible alias; the single implementation lives in apps.utils.fields.
    return fields_sanitize_json_data(data)


def get_evaluator_type_info() -> dict[str, dict[str, str | None]]:
    """
    Get evaluator type information (label, icon) for all available evaluator classes.

    Returns:
        Dict mapping evaluator class names to their schema info (label, icon)
    """
    from apps.evaluations import evaluators  # noqa: PLC0415 - circular: evaluators imports evaluations.utils

    evaluator_classes = [
        cls
        for _, cls in inspect.getmembers(evaluators, inspect.isclass)
        if issubclass(cls, evaluators.BaseEvaluator) and cls != evaluators.BaseEvaluator
    ]

    evaluator_type_info = {}
    for cls in evaluator_classes:
        evaluator_schema = cls.model_config.get("evaluator_schema")
        if evaluator_schema:
            evaluator_type_info[cls.__name__] = {
                "label": evaluator_schema.label,
                "icon": evaluator_schema.icon,
            }

    return evaluator_type_info


def get_evaluators_with_schema(team) -> list[dict]:
    """
    Get all evaluators for a team with their type information including labels and icons.

    Args:
        team: The team to filter evaluators for

    Returns:
        List of dicts containing evaluator info with schema data
    """
    from apps.evaluations.models import (  # noqa: PLC0415 - circular: evaluations.models imports evaluations.utils
        Evaluator,
    )

    evaluator_type_info = get_evaluator_type_info()

    evaluators_list = []
    for evaluator in Evaluator.objects.filter(team=team):
        type_info = evaluator_type_info.get(evaluator.type, {})
        evaluators_list.append(
            {
                "id": evaluator.id,
                "name": evaluator.name,
                "type": evaluator.type,
                "label": type_info.get("label", evaluator.type),
                "icon": type_info.get("icon"),
            }
        )

    return evaluators_list


def get_evaluator_type_display(evaluator_type: str) -> dict[str, str | None]:
    """
    Get display information for a single evaluator type.

    Args:
        evaluator_type: The class name of the evaluator type

    Returns:
        Dict with label and icon for the evaluator type
    """
    evaluator_type_info = get_evaluator_type_info()
    return evaluator_type_info.get(evaluator_type, {"label": evaluator_type, "icon": None})


def parse_history_text(history_text: str) -> list:
    """Parse history text back into JSON format for EvaluationMessage.history field."""

    history = []
    if not history_text.strip():
        return history

    # Validate that history text starts with user: or assistant:
    first_line = history_text.strip().lower()
    if not (first_line.startswith("user:") or first_line.startswith("assistant:")):
        raise HistoryParseException

    current_message = None

    for line in history_text.split("\n"):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        if line_stripped.lower().startswith((f"{ChatMessageType.HUMAN.role}:", f"{ChatMessageType.AI.role}:")):
            if current_message:
                history.append(current_message)

            colon_position = line_stripped.find(":")
            role = line_stripped[:colon_position].strip().lower()
            content = line_stripped[colon_position + 1 :].strip()
            current_message = {
                "message_type": ChatMessageType.from_role(role),
                "content": content,
                "summary": None,
            }
        elif current_message:
            # Continuation of current message content
            current_message["content"] = cast(str, current_message["content"]) + "\n" + line_stripped

    if current_message:
        history.append(current_message)

    if not history:
        raise HistoryParseException
    return history


def generate_csv_column_suggestions(columns):
    """Generate smart suggestions for column mapping based on column names."""
    suggestions = {
        "context": [],
        "participant_data": [],
        "session_state": [],
    }
    input_patterns = {"input", "human", "user", "question", "prompt", "message", "query"}
    output_patterns = {"output", "ai", "assistant", "response", "answer", "reply", "completion"}

    for col in columns:
        col_lower = col.lower().strip()
        if "input" not in suggestions and any(pattern in col_lower for pattern in input_patterns):
            suggestions["input"] = col
        elif "output" not in suggestions and any(pattern in col_lower for pattern in output_patterns):
            suggestions["output"] = col
        elif col_lower == "id":
            # Skip suggesting ID columns
            continue
        elif col_lower == "history":
            # History has its own suggestion mechanism
            suggestions["history"] = col
        elif col.startswith("context."):
            field_name = _clean_field_name(col.removeprefix("context."))
            suggestions["context"].append({"fieldName": field_name, "csvColumn": col})
        elif col.startswith("participant_data."):
            field_name = _clean_field_name(col.removeprefix("participant_data."))
            suggestions["participant_data"].append({"fieldName": field_name, "csvColumn": col})
        elif col.startswith("session_state."):
            field_name = _clean_field_name(col.removeprefix("session_state."))
            suggestions["session_state"].append({"fieldName": field_name, "csvColumn": col})
        else:
            # Fall back to suggesting unknown fields as context fields
            field_name = _clean_field_name(col)
            suggestions["context"].append({"fieldName": field_name, "csvColumn": col})

    return suggestions


def _clean_field_name(field_name):
    """Clean a field name to be a valid Python identifier."""
    # Convert spaces to underscores and remove invalid characters
    field_name = re.sub(r"[^\w]", "_", field_name)

    # Ensure it starts with a letter or underscore
    if field_name and not field_name[0].isalpha() and field_name[0] != "_":
        field_name = f"_{field_name}"

    # Remove consecutive underscores and trailing underscores
    field_name = re.sub(r"_+", "_", field_name).strip("_")

    return field_name or "context_variable"


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
    from apps.evaluations.models import (  # noqa: PLC0415 - circular: evaluations.models imports evaluations.utils
        EvaluationMessage,
    )

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


def make_session_evaluation_messages(session_external_ids: list[str], team=None) -> list["EvaluationMessage"]:
    """Eager wrapper around iter_session_evaluation_messages.

    Only safe for small session counts; large jobs should consume the generator so memory
    stays bounded.
    """
    return list(iter_session_evaluation_messages(session_external_ids, team=team))


def make_evaluation_messages_from_sessions(message_ids_per_session: dict[str, list[str]]) -> list["EvaluationMessage"]:
    from apps.evaluations.models import (  # noqa: PLC0415 - circular: evaluations.models imports evaluations.utils
        EvaluationMessage,
        EvaluationMessageContent,
    )

    session_map = {
        str(s.external_id): s for s in ExperimentSession.objects.filter(external_id__in=message_ids_per_session.keys())
    }

    def _add_additional_context(msg, existing_context):
        if comments := list(msg.comments.all()):
            existing_context.setdefault("comments", []).extend([comment.comment for comment in comments])
        if tags := list(msg.tags.all()):
            context_tags = existing_context.get("tags", [])
            context_tags.extend([tag.name for tag in tags if not tag.is_system_tag])
            existing_context["tags"] = list(dict.fromkeys(context_tags))  # dedupe preserving order

    def _messages_to_history(messages_):
        return [
            {
                "message_type": msg.message_type,
                "content": msg.content,
                "summary": getattr(msg, "summary", None),
            }
            for msg in messages_
        ]

    new_messages = []
    for session_id, target_message_ids in message_ids_per_session.items():
        target_ids_set = set(target_message_ids)

        # We need to get all the messages in the session to properly compile the history
        all_messages = list(
            ChatMessage.objects.filter(chat__experiment_session__external_id=session_id)
            .annotate(
                experiment_public_id=F("chat__experiment_session__experiment__public_id"),
                participant_data=F("input_message_trace__participant_data"),
                session_state=F("input_message_trace__session_state"),
            )
            .prefetch_related("comments", "tags", "input_message_trace")
            .order_by("created_at")
        )

        history = []
        i = 0

        while i < len(all_messages):
            current_msg = all_messages[i]
            next_msg = all_messages[i + 1] if i + 1 < len(all_messages) else None

            # Check if this is a (HUMAN, AI) pair with at least one in the target
            is_target_pair = (
                next_msg is not None
                and current_msg.message_type == ChatMessageType.HUMAN
                and next_msg.message_type == ChatMessageType.AI
                and (current_msg.id in target_ids_set or next_msg.id in target_ids_set)
            )

            shared_attrs = {
                "session": session_map.get(str(session_id)),
                "history": [msg.copy() for msg in history],
                "metadata": {
                    "session_id": session_id,
                    "experiment_id": str(current_msg.experiment_public_id),
                },
                "participant_data": current_msg.participant_data or {},
                "session_state": current_msg.session_state or {},
            }

            if is_target_pair:
                # Create paired evaluation message
                context = {"current_datetime": current_msg.created_at.isoformat()}
                _add_additional_context(current_msg, context)
                _add_additional_context(next_msg, context)

                evaluation_message = EvaluationMessage(
                    input_chat_message=current_msg,
                    input=EvaluationMessageContent(content=current_msg.content, role="human").model_dump(),
                    expected_output_chat_message=next_msg,
                    output=EvaluationMessageContent(content=next_msg.content, role="ai").model_dump(),
                    context=context,
                    **shared_attrs,
                )
                new_messages.append(evaluation_message)

                # Add both to history
                history.extend(_messages_to_history([current_msg, next_msg]))
                i += 2

            elif current_msg.id in target_ids_set:
                context = {"current_datetime": current_msg.created_at.isoformat()}
                _add_additional_context(current_msg, context)

                if current_msg.message_type == ChatMessageType.HUMAN:
                    # There is an orphaned Human message, possibly because the AI message failed to generate
                    evaluation_message = EvaluationMessage(
                        input_chat_message=current_msg,
                        input=EvaluationMessageContent(content=current_msg.content, role="human").model_dump(),
                        expected_output_chat_message=None,
                        output={},
                        context=context,
                        **shared_attrs,
                    )
                else:
                    # There is an orphaned AI message, possibly because of a scheduled message, AI seed, etc.
                    evaluation_message = EvaluationMessage(
                        input_chat_message=None,
                        input={},
                        expected_output_chat_message=current_msg,
                        output=EvaluationMessageContent(content=current_msg.content, role="ai").model_dump(),
                        context=context,
                        **shared_attrs,
                    )
                new_messages.append(evaluation_message)

                # Add to history
                history.extend(_messages_to_history([current_msg]))
                i += 1

            else:
                # Not in target, just add to history
                history.extend(_messages_to_history([current_msg]))
                i += 1

    return new_messages


def normalize_json_quotes(text):
    """Normalize fancy quotes to regular quotes for JSON parsing."""
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("‘", "'").replace("’", "'")
    return text


def parse_csv_value_as_json(value):
    """Parse value as JSON if it's an object or array, otherwise return as-is."""
    if not value:
        return value
    # Only parse if it looks like a JSON object or array
    value_stripped = value.strip()
    if value_stripped.startswith(("{", "[")):
        try:
            normalized_value = normalize_json_quotes(value)
            return json.loads(normalized_value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def schema_to_pydantic_model(schema: dict[str, FieldDefinition], model_name: str = "DynamicModel") -> type[BaseModel]:
    """Converts a typed schema dictionary to a Pydantic model.

    Expected format:
        {
            "field_name": FieldDefinition(...)
        }

    Args:
        schema: Dictionary mapping field names to FieldDefinition objects
        model_name: Name for the generated Pydantic model

    Returns:
        Dynamically created Pydantic BaseModel class
    """

    pydantic_fields = {}

    for field_name, field_def in schema.items():
        pydantic_fields[field_name] = (
            field_def.python_type,
            Field(**field_def.pydantic_fields),
        )

    return create_model(model_name, **pydantic_fields)


def get_use_in_aggregations(field_def: dict) -> bool:
    """Get the use_in_aggregations setting for a field, with type-based defaults.

    Defaults: string=False, others (int, float, choice)=True
    """
    if "use_in_aggregations" in field_def:
        return field_def["use_in_aggregations"]
    return field_def.get("type") != "string"


def filter_aggregates_for_display(aggregates) -> list[dict]:
    """Filter aggregate fields based on use_in_aggregations setting.

    Returns a list of dicts with evaluator info and filtered aggregates.
    """
    result = []
    for agg in aggregates:
        output_schema = agg.evaluator.params.get("output_schema", {})
        filtered = {
            field_name: stats
            for field_name, stats in agg.aggregates.items()
            if get_use_in_aggregations(output_schema.get(field_name, {}))
        }
        result.append(
            {
                "evaluator": agg.evaluator,
                "aggregates": filtered,
            }
        )
    return result


def build_trend_data(runs: list) -> dict:
    """Build trend data for displaying charts from evaluation runs.

    Args:
        runs: List of EvaluationRun objects with prefetched aggregates__evaluator

    Returns:
        {
            "evaluator_name": {
                "field_name (type)": {
                    "type": "numeric" | "categorical",
                    "points": [{"run_id": int, "date": str, "value": any, ...}],
                    "categories": ["cat1", "cat2"],  # for categorical only
                    "mean": float,  # for numeric only
                }
            }
        }

    Note:
        Fields are keyed by (field_name, type) to handle cases where a field's
        type changes between runs (e.g., from numeric to categorical). This
        ensures each type gets its own trend line/chart.
    """
    if not runs:
        return {}

    trend = defaultdict(
        lambda: defaultdict(
            lambda: {
                "type": None,
                "points": [],
                "categories": set(),
            }
        )
    )

    for run in runs:
        for agg in run.aggregates.all():
            evaluator_name = agg.evaluator.name
            output_schema = agg.evaluator.params.get("output_schema", {})

            for field_name, stats in agg.aggregates.items():
                if not isinstance(stats, dict) or "type" not in stats:
                    continue

                if not get_use_in_aggregations(output_schema.get(field_name, {})):
                    continue

                field_type = stats["type"]
                field_key = (field_name, field_type)
                field = trend[evaluator_name][field_key]
                field["type"] = field_type

                # Build point data
                point = {
                    "run_id": run.id,
                    "date": run.created_at.strftime("%b %d"),
                }

                if field_type == "numeric":
                    point["value"] = stats.get("mean")
                else:
                    point["value"] = stats.get("mode")
                    point["distribution"] = stats.get("distribution", {})
                    # Collect categories
                    if stats.get("distribution"):
                        field["categories"].update(stats["distribution"].keys())

                field["points"].append(point)

    # Post-process: convert to regular dicts, sort categories, calculate means
    result = {}
    for evaluator_name, fields in trend.items():
        result[evaluator_name] = {}
        for (field_name, field_type), field_data in fields.items():
            processed = {
                "type": field_data["type"],
                "points": field_data["points"],
            }

            if field_data["type"] == "categorical":
                processed["categories"] = sorted(field_data["categories"])
                # Calculate overall mode across all runs
                all_modes = [p["value"] for p in field_data["points"] if p["value"] is not None]
                if all_modes:
                    processed["mode"] = Counter(all_modes).most_common(1)[0][0]
            else:
                # Calculate mean for numeric fields
                values = [p["value"] for p in field_data["points"] if p["value"] is not None]
                if values:
                    processed["mean"] = round(sum(values) / len(values), 2)

            # Use "field_name (type)" as key to distinguish different types
            display_key = f"{field_name} ({field_type})"
            result[evaluator_name][display_key] = processed

    return result
