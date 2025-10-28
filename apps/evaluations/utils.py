import inspect
import json
import re
from typing import TYPE_CHECKING, Any

from django.db.models import F

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.exceptions import HistoryParseException

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
    if isinstance(data, dict):
        return {key: sanitize_json_data(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [sanitize_json_data(item) for item in data]
    elif isinstance(data, str):
        # Remove null bytes and control characters (except common whitespace like \n, \r, \t)
        # This removes characters in the range \x00-\x1f except \t (0x09), \n (0x0a), \r (0x0d)
        sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data)
        return sanitized
    else:
        # Return primitives (int, float, bool, None) as-is
        return data


def get_evaluator_type_info() -> dict[str, dict[str, str]]:
    """
    Get evaluator type information (label, icon) for all available evaluator classes.

    Returns:
        Dict mapping evaluator class names to their schema info (label, icon)
    """
    from apps.evaluations import evaluators

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
    from apps.evaluations.models import Evaluator

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


def get_evaluator_type_display(evaluator_type: str) -> dict[str, str]:
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
            current_message["content"] += "\n" + line_stripped

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


def make_evaluation_messages_from_sessions(message_ids_per_session: dict[str, list[str]]) -> list["EvaluationMessage"]:
    from apps.evaluations.models import EvaluationMessage, EvaluationMessageContent

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
