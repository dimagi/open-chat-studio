import inspect
import json
import logging
import re
from collections import Counter, defaultdict
from typing import Any, cast

from pydantic import BaseModel, Field

from apps.chat.models import ChatMessageType
from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.field_definitions import FieldDefinition
from apps.evaluations.models import Evaluator
from apps.utils.fields import sanitize_json_data as fields_sanitize_json_data
from apps.utils.schema_utils import create_model_with_sanitized_names

logger = logging.getLogger("ocs.evaluations")


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
    evaluator_type_info = get_evaluator_type_info()

    evaluators_list = []
    for evaluator in Evaluator.objects.filter(team=team, is_archived=False):
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
    if not first_line.startswith(("user:", "assistant:")):
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
            current_message["content"] = cast("str", current_message["content"]) + "\n" + line_stripped

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


def normalize_json_quotes(text):
    """Normalize fancy quotes to regular quotes for JSON parsing."""
    text = text.replace("“", '"').replace("”", '"')
    return text.replace("‘", "'").replace("’", "'")


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

    Field names are sanitized (see `create_model_with_sanitized_names`) so the model's JSON schema
    keys are valid tool/property names for every provider (notably Anthropic's
    `^[a-zA-Z0-9_.-]{1,64}$`). A plain `model_dump()` on an instance of the returned model already
    comes back keyed by the original field names, so callers don't need to translate an LLM result
    back themselves.

    Args:
        schema: Dictionary mapping field names to FieldDefinition objects
        model_name: Name for the generated Pydantic model

    Returns:
        Dynamically created Pydantic BaseModel class
    """
    fields = {
        field_name: (field_def.python_type, Field(**field_def.pydantic_fields))
        for field_name, field_def in schema.items()
    }
    return create_model_with_sanitized_names(model_name, fields)


def get_use_in_aggregations(field_def: dict) -> bool:
    """Get the use_in_aggregations setting for a field, with type-based defaults.

    Defaults: string=False, others (int, float, choice)=True
    """
    if "use_in_aggregations" in field_def:
        return field_def["use_in_aggregations"]
    return field_def.get("type") != "string"


def merge_binary_labels(stats: dict, field_def: dict) -> dict:
    """Copy binary display labels from a field definition into an aggregate blob.

    Labels live only in the schema; the stored blob carries integers. `false_count`
    is derived here rather than stored so blobs aggregated before it existed still
    render both counts. Non-binary blobs pass through unchanged.
    """
    if not isinstance(stats, dict) or stats.get("type") != "binary":
        return stats
    merged = {
        **stats,
        "true_label": field_def.get("true_label", "True"),
        "false_label": field_def.get("false_label", "False"),
    }
    if stats.get("true_count") is not None:
        merged["false_count"] = stats["count"] - stats["true_count"]
    return merged


def filter_aggregates_for_display(aggregates) -> list[dict]:
    """Filter aggregate fields based on use_in_aggregations setting.

    Returns a list of dicts with evaluator info and filtered aggregates.
    """
    result = []
    for agg in aggregates:
        output_schema = agg.evaluator.params.get("output_schema", {})
        filtered = {
            field_name: merge_binary_labels(stats, output_schema.get(field_name) or {})
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
                    "type": "numeric" | "categorical" | "binary",
                    "points": [{"run_id": int, "date": str, "value": any, ...}],
                    "categories": ["cat1", "cat2"],  # for categorical only
                    "mean": float,  # for numeric and binary only
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

                if field_type in ("numeric", "binary"):
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
