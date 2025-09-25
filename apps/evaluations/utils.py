import inspect
import re

from django.db.models import QuerySet

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.exceptions import HistoryParseException


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
    suggestions = {}
    input_patterns = {"input", "human", "user", "question", "prompt", "message", "query"}
    output_patterns = {"output", "ai", "assistant", "response", "answer", "reply", "completion"}

    context_columns = []

    for col in columns:
        col_lower = col.lower().strip()
        if "input" not in suggestions and any(pattern in col_lower for pattern in input_patterns):
            suggestions["input"] = col
        elif "output" not in suggestions and any(pattern in col_lower for pattern in output_patterns):
            suggestions["output"] = col
        elif col_lower == "id":
            # Skip suggesting ID columns as context
            continue
        elif col_lower == "history":
            # History has its own suggestion mechanism
            suggestions["history"] = col
        else:
            # Clean up column name for context field suggestion
            clean_name = _clean_context_field_name(col)
            context_columns.append({"fieldName": clean_name, "csvColumn": col})

    if context_columns:
        suggestions["context"] = context_columns

    return suggestions


def _clean_context_field_name(field_name):
    """Clean a field name to be a valid Python identifier."""
    if field_name.lower().startswith("context."):
        field_name = field_name[8:]  # Remove 'context.' prefix

    # Convert spaces to underscores and remove invalid characters
    field_name = re.sub(r"[^\w]", "_", field_name)

    # Ensure it starts with a letter or underscore
    if field_name and not field_name[0].isalpha() and field_name[0] != "_":
        field_name = f"_{field_name}"

    # Remove consecutive underscores and trailing underscores
    field_name = re.sub(r"_+", "_", field_name).strip("_")

    return field_name or "context_variable"


def make_message_pairs_from_queryset(queryset: QuerySet) -> list[ChatMessage]:
    """Takes a queryset of ChatMessages, and adds extra messages such that we always have (Human, AI) message pairs.
    There can be a single AI Message at the beginning (AI seed message).
    All messages must be from the same chat.
    """

    if not queryset:
        return []

    queryset_list = list(queryset)
    chat = queryset_list[0].chat

    all_chat_messages = list(chat.messages.order_by("created_at"))
    message_lookup = {msg.id: i for i, msg in enumerate(all_chat_messages)}

    def get_previous_message(message):
        current_index = message_lookup[message.id]
        return all_chat_messages[current_index - 1] if current_index > 0 else None

    def get_next_message(message):
        current_index = message_lookup[message.id]
        return all_chat_messages[current_index + 1] if current_index < len(all_chat_messages) - 1 else None

    all_messages = set()

    # Handle AI seed message
    has_ai_seed_message = False
    if queryset_list[0].is_first_message and queryset_list[0].is_ai_message:
        has_ai_seed_message = True
        all_messages.add(queryset_list[0])

    starting_index = 1 if has_ai_seed_message else 0
    for message in queryset_list[starting_index:]:
        all_messages.add(message)  # Add the current message

        if message.is_ai_message:
            previous_message = get_previous_message(message)
            if previous_message and previous_message.message_type == ChatMessageType.HUMAN:
                all_messages.add(previous_message)
            else:
                raise ValueError(f"AI message {message.id} has no corresponding human message")

        elif message.is_human_message:
            next_message = get_next_message(message)
            if next_message and next_message.message_type == ChatMessageType.AI:
                all_messages.add(next_message)
            else:
                raise ValueError(f"Human message {message.id} has no corresponding AI message")

    return list(all_messages)
