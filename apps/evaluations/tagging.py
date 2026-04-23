"""Eval-driven tagging: pure predicate logic + DB orchestration.

Pure helpers (no Django DB access) live at the top so they can be unit-tested
without `@pytest.mark.django_db`. The DB-touching orchestrator lives at the
bottom and is called from the evaluation task.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from apps.annotations.models import CustomTaggedItem, TagCategories
from apps.evaluations.field_definitions import (
    ChoiceFieldDefinition,
    FieldDefinition,
    FloatFieldDefinition,
    IntFieldDefinition,
    StringFieldDefinition,
)
from apps.evaluations.models import ConditionType

if TYPE_CHECKING:
    from apps.annotations.models import Tag
    from apps.chat.models import Chat, ChatMessage
    from apps.evaluations.models import (
        EvaluationMessage,
        EvaluationResult,
        Evaluator,
        EvaluatorTagRule,
    )

logger = logging.getLogger("ocs.evaluations.tagging")

_FIELD_DEFINITION_ADAPTER = TypeAdapter(FieldDefinition)

_NUMERIC_FIELD_TYPES = (IntFieldDefinition, FloatFieldDefinition)


def parse_field_definition(field_def: dict | FieldDefinition) -> FieldDefinition:
    """Parse a raw dict into a typed FieldDefinition, or return as-is if already one."""
    if isinstance(field_def, (IntFieldDefinition, FloatFieldDefinition, ChoiceFieldDefinition)):
        return field_def
    # Let pydantic pick the right variant from the discriminated union via `type`.
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
    """Raise ValidationError when the condition is malformed for the given field."""
    if not isinstance(condition_value, dict):
        raise ValidationError({"condition_value": "Condition value must be a JSON object."})

    if condition_type == ConditionType.EQUALS:
        _validate_equals_condition(condition_value, field_definition)
    elif condition_type == ConditionType.RANGE:
        _validate_range_condition(condition_value, field_definition)
    else:
        raise ValidationError({"condition_type": f"Unknown condition type: {condition_type}"})


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
        try:
            field_definition.python_type(condition_value["value"])
        except (TypeError, ValueError) as err:
            raise ValidationError(
                {"condition_value": (f"Value must be coercible to {field_definition.python_type.__name__}.")}
            ) from err
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
    try:
        lo = py_type(condition_value["min"])
        hi = py_type(condition_value["max"])
    except (TypeError, ValueError) as err:
        raise ValidationError({"condition_value": f"min/max must be coercible to {py_type.__name__}."}) from err
    if lo > hi:
        raise ValidationError({"condition_value": "'min' must be <= 'max'."})


def validate_tag_compatibility(tag: Tag, evaluator: Evaluator) -> None:
    """Ensure the tag is compatible with this evaluator (category, team, system flag)."""
    if tag.team_id != evaluator.team_id:
        raise ValidationError({"tag": "Tag must belong to the same team as the evaluator."})
    if tag.category != TagCategories.EVALUATIONS:
        raise ValidationError({"tag": f"Tag must be in the '{TagCategories.EVALUATIONS}' category."})
    if not tag.is_system_tag:
        raise ValidationError({"tag": "Tag must be a system tag."})


def matches(condition_type: str, condition_value: dict, field_value: Any) -> bool:
    """Return True if field_value satisfies the condition. Raises on unknown type."""
    if condition_type == ConditionType.EQUALS:
        return field_value == condition_value.get("value")
    if condition_type == ConditionType.RANGE:
        try:
            numeric = float(field_value)
        except (TypeError, ValueError):
            return False
        return float(condition_value["min"]) <= numeric <= float(condition_value["max"])
    raise ValueError(f"Unknown condition type: {condition_type}")


def evaluate_rules(rules: list[EvaluatorTagRule], result_output: dict) -> list[EvaluatorTagRule]:
    """Return the subset of rules that match the given evaluator result output.

    Rules whose field_name is absent or whose condition doesn't apply cleanly
    are skipped with a logged warning (defensive against schema drift).
    """
    matched: list[EvaluatorTagRule] = []
    result_dict = (result_output or {}).get("result") or {}

    for rule in rules:
        if rule.field_name not in result_dict:
            logger.warning(
                "Skipping tag rule %s: field '%s' not present in evaluator result.",
                rule.pk,
                rule.field_name,
            )
            continue
        field_value = result_dict[rule.field_name]
        try:
            if matches(rule.condition_type, rule.condition_value or {}, field_value):
                matched.append(rule)
        except (ValueError, TypeError) as exc:
            logger.warning("Skipping tag rule %s due to evaluation error: %s", rule.pk, exc)
    return matched


def resolve_target(evaluator: Evaluator, evaluation_message: EvaluationMessage) -> Chat | ChatMessage | None:
    """Return the object to tag based on the evaluator's mode, or None if no target."""
    from apps.evaluations.models import EvaluationMode  # noqa: PLC0415

    if evaluator.evaluation_mode == EvaluationMode.SESSION:
        session = evaluation_message.session
        if session is None:
            return None
        return session.chat
    return evaluation_message.expected_output_chat_message


def apply_rules_to_result(
    evaluation_result: EvaluationResult,
    evaluator: Evaluator,
    evaluation_message: EvaluationMessage,
) -> None:
    """Apply this evaluator's tag rules to the target, removing stale tags and recording audit rows.

    Caller is responsible for running this inside a transaction.atomic() block along
    with the EvaluationResult.create() it corresponds to.
    """
    from apps.evaluations.models import AppliedTag  # noqa: PLC0415

    rules = list(evaluator.tag_rules.all())
    if not rules:
        return

    target = resolve_target(evaluator, evaluation_message)
    if target is None:
        return

    matched_rules = evaluate_rules(rules, evaluation_result.output or {})
    tags_to_apply = {rule.tag_id: rule.tag for rule in matched_rules}
    rule_tag_ids = {rule.tag_id for rule in rules}
    tags_to_remove = rule_tag_ids - set(tags_to_apply.keys())

    content_type = ContentType.objects.get_for_model(type(target))

    if tags_to_remove:
        CustomTaggedItem.objects.filter(
            content_type=content_type,
            object_id=target.pk,
            tag_id__in=tags_to_remove,
            tag__category=TagCategories.EVALUATIONS,
        ).delete()

    team = evaluation_result.team
    if tags_to_apply:
        CustomTaggedItem.objects.bulk_create(
            [
                CustomTaggedItem(
                    content_type=content_type,
                    object_id=target.pk,
                    tag=tag,
                    team=team,
                )
                for tag in tags_to_apply.values()
            ],
            ignore_conflicts=True,
        )

    if matched_rules:
        AppliedTag.objects.bulk_create(
            [
                AppliedTag(
                    team=team,
                    evaluation_result=evaluation_result,
                    rule=rule,
                    tag=rule.tag,
                )
                for rule in matched_rules
            ]
        )
