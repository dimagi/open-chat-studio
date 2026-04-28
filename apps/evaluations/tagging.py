"""Eval-driven tagging: DB orchestration.

Pure validators live in `rule_validation.py`. The DB-touching orchestrator
at the bottom is called from the evaluation task.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.contrib.contenttypes.models import ContentType

from apps.annotations.models import CustomTaggedItem
from apps.evaluations.models import AppliedTag, ConditionType, EvaluationMode

if TYPE_CHECKING:
    from apps.chat.models import Chat, ChatMessage
    from apps.evaluations.models import (
        EvaluationMessage,
        EvaluationResult,
        Evaluator,
        EvaluatorTagRule,
    )

logger = logging.getLogger("ocs.evaluations.tagging")


def matches(condition_type: str, condition_value: dict, field_value: Any) -> bool:
    """Return True if field_value satisfies the condition. Raises on unknown type."""
    try:
        ct = ConditionType(condition_type)
    except ValueError as err:
        raise ValueError(f"Unknown condition type: {condition_type}") from err
    return ct.matches(condition_value, field_value)


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
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Skipping tag rule %s due to evaluation error: %s", rule.pk, exc)
    return matched


def resolve_target(evaluator: Evaluator, evaluation_message: EvaluationMessage) -> Chat | ChatMessage | None:
    """Return the object to tag based on the evaluator's mode, or None if no target.

    SESSION mode returns the session's `Chat` (not the `ExperimentSession` itself) because
    `Chat` owns the TaggedModelMixin contract — tags live on the chat, not the session row.
    """
    if evaluator.evaluation_mode == EvaluationMode.SESSION:
        session = evaluation_message.session
        if session is None:
            return None
        return session.chat
    return evaluation_message.expected_output_chat_message


def _get_cached_tag_rules(evaluator: Evaluator) -> list[EvaluatorTagRule]:
    """Cached fetch of the evaluator's tag rules to skip the query on repeat calls."""
    rules = getattr(evaluator, "_tag_rules_cache", None)
    if rules is None:
        rules = list(evaluator.tag_rules.all())
        evaluator._tag_rules_cache = rules
    return rules


def apply_rules_to_result(
    evaluation_result: EvaluationResult,
    evaluator: Evaluator,
    evaluation_message: EvaluationMessage,
) -> None:
    """Apply this evaluator's tag rules to the target and record audit rows.

    Caller is responsible for running this inside a transaction.atomic() block along
    with the EvaluationResult.create() it corresponds to.
    """
    rules = _get_cached_tag_rules(evaluator)
    if not rules:
        return

    target = resolve_target(evaluator, evaluation_message)
    if target is None:
        return

    matched_rules = evaluate_rules(rules, evaluation_result.output or {})
    if not matched_rules:
        return

    tags_to_apply = {rule.tag_id: rule.tag for rule in matched_rules}
    content_type = ContentType.objects.get_for_model(type(target))
    team = evaluation_result.team

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
