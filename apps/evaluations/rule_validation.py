"""Pure validators for evaluator tag rules.

Extracted from `tagging.py` so that `models.py` can import validators at module
level without circular imports (models → rule_validation; tagging → models).
All helpers in this module are DB-free. `ConditionType` also lives here so
both `models.py` and validators can share the same enum without a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from apps.evaluations.field_definitions import (
    ChoiceFieldDefinition,
    FieldDefinition,
    FloatFieldDefinition,
    IntFieldDefinition,
    StringFieldDefinition,
)

if TYPE_CHECKING:
    from apps.annotations.models import Tag
    from apps.evaluations.models import Evaluator


_FIELD_DEFINITION_ADAPTER = TypeAdapter(FieldDefinition)

_NUMERIC_FIELD_TYPES = (IntFieldDefinition, FloatFieldDefinition)


class ConditionType(models.TextChoices):
    EQUALS = "equals", "Equals"
    RANGE = "range", "Range"

    @staticmethod
    def coerce_value(raw, field_type: str | None):
        """Coerce a raw equals-value input to the target field's python type.

        Returns the input unchanged when `field_type` isn't numeric; raises
        (TypeError, ValueError) on failed int/float conversion so the form can
        surface a user-facing error.
        """
        if field_type == "int":
            return int(raw)
        if field_type == "float":
            return float(raw)
        return raw

    def matches(self, condition_value: dict, field_value) -> bool:
        """Return True if field_value satisfies this condition."""
        if self == ConditionType.EQUALS:
            return field_value == condition_value.get("value")
        if self == ConditionType.RANGE:
            try:
                numeric = float(field_value)
                lo = float(condition_value.get("min"))
                hi = float(condition_value.get("max"))
            except (TypeError, ValueError):
                return False
            return lo <= numeric <= hi
        raise ValueError(f"Unknown condition type: {self}")


def parse_field_definition(field_def: dict | FieldDefinition) -> FieldDefinition:
    """Parse a raw dict into a typed FieldDefinition, or return as-is if already one."""
    if isinstance(field_def, (IntFieldDefinition, FloatFieldDefinition, ChoiceFieldDefinition)):
        return field_def
    return _FIELD_DEFINITION_ADAPTER.validate_python(field_def)


def validate_field_in_schema(field_name: str, output_schema: dict) -> FieldDefinition:
    """Ensure field_name exists in output_schema and return its FieldDefinition."""
    if not field_name:
        raise ValidationError({"field_name": "Field name is required."})
    if field_name not in output_schema:
        raise ValidationError({"field_name": f"Field '{field_name}' is not defined in the evaluator's output schema."})
    raw = output_schema[field_name]
    try:
        return parse_field_definition(raw)
    except PydanticValidationError as err:
        raise ValidationError({"field_name": f"Field '{field_name}' has an invalid definition: {err}"}) from err


def validate_condition(
    condition_type: str,
    condition_value: Any,
    field_definition: FieldDefinition,
) -> None:
    """Raise ValidationError when the condition is malformed for the given field.

    Mutates condition_value in place to coerce values to the field's native python_type
    so stored rules match the type of the evaluator output and `==` comparisons behave.
    """
    if not isinstance(condition_value, dict):
        raise ValidationError({"condition_value": "Condition value must be a JSON object."})

    if condition_type == ConditionType.EQUALS:
        _validate_equals_condition(condition_value, field_definition)
    elif condition_type == ConditionType.RANGE:
        _validate_range_condition(condition_value, field_definition)
    else:
        raise ValidationError({"condition_type": f"Unknown condition type: {condition_type}"})


def _coerce_numeric(raw: Any, py_type: type) -> int | float:
    """Coerce raw to py_type, rejecting non-integral values when py_type is int."""
    if py_type is int and isinstance(raw, float) and not raw.is_integer():
        raise ValidationError({"condition_value": f"Value must be an integer (got {raw!r})."})
    if py_type is int and isinstance(raw, str):
        try:
            as_float = float(raw)
        except (TypeError, ValueError) as err:
            raise ValidationError({"condition_value": "Value must be coercible to int."}) from err
        if not as_float.is_integer():
            raise ValidationError({"condition_value": f"Value must be an integer (got {raw!r})."})
        return int(as_float)
    try:
        return py_type(raw)
    except (TypeError, ValueError) as err:
        raise ValidationError({"condition_value": f"Value must be coercible to {py_type.__name__}."}) from err


def _validate_equals_condition(condition_value: dict, field_definition: FieldDefinition) -> None:
    extra = set(condition_value.keys()) - {"value"}
    if extra:
        raise ValidationError({"condition_value": f"Extra keys for 'equals' condition: {sorted(extra)}"})
    if "value" not in condition_value:
        raise ValidationError({"condition_value": "'equals' condition requires a 'value' key."})
    if isinstance(field_definition, ChoiceFieldDefinition):
        if not field_definition.choices:
            raise ValidationError({"condition_value": "Choice field has no 'choices' configured."})
        if condition_value["value"] not in field_definition.choices:
            raise ValidationError(
                {
                    "condition_value": (
                        f"Value '{condition_value['value']}' is not in the field's choices: {field_definition.choices}"
                    )
                }
            )
        return
    if isinstance(field_definition, _NUMERIC_FIELD_TYPES):
        condition_value["value"] = _coerce_numeric(condition_value["value"], field_definition.python_type)
        return
    if isinstance(field_definition, StringFieldDefinition):
        if not isinstance(condition_value["value"], str):
            raise ValidationError({"condition_value": "Value must be a string."})
        return
    raise ValidationError({"condition_type": "'equals' condition is not supported for this field type."})


def _validate_range_condition(condition_value: dict, field_definition: FieldDefinition) -> None:
    extra = set(condition_value.keys()) - {"min", "max"}
    if extra:
        raise ValidationError({"condition_value": f"Extra keys for 'range' condition: {sorted(extra)}"})
    if "min" not in condition_value or "max" not in condition_value:
        raise ValidationError({"condition_value": "'range' condition requires both 'min' and 'max' keys."})
    if not isinstance(field_definition, _NUMERIC_FIELD_TYPES):
        raise ValidationError({"condition_type": "'range' condition is only valid for numeric (int/float) fields."})
    py_type = field_definition.python_type
    lo = _coerce_numeric(condition_value["min"], py_type)
    hi = _coerce_numeric(condition_value["max"], py_type)
    if lo > hi:
        raise ValidationError({"condition_value": "'min' must be <= 'max'."})
    condition_value["min"] = lo
    condition_value["max"] = hi


def validate_tag_compatibility(tag: Tag, evaluator: Evaluator) -> None:
    """Ensure the tag is compatible with this evaluator (team, not a system tag).

    The team check is defensive: the form/factory paths already set
    `tag.team == evaluator.team`, but admin/shell/ORM callers can create
    cross-team rules that this catches before a bad row lands.
    """
    if tag.team_id != evaluator.team_id:
        raise ValidationError({"tag": "Tag must belong to the same team as the evaluator."})
    if tag.is_system_tag:
        raise ValidationError({"tag": "Tag must not be a system tag."})
