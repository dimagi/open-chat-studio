"""Eval-driven tagging: DB orchestration.

Pure validators live in `rule_validation.py`. The DB-touching orchestrator
at the bottom is called from the evaluation task.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from apps.annotations.models import CustomTaggedItem
from apps.evaluations.models import (
    AppliedTag,
    ConditionType,
    EvaluationMode,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
)

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


def _get_possible_tags(evaluators: list[Evaluator]) -> frozenset[int]:
    return frozenset(rule.tag_id for evaluator in evaluators for rule in evaluator.tag_rules.all())


def _build_applied_by_message(run: EvaluationRun) -> defaultdict[int, set[int]]:
    """Batch all AppliedTag lookups for this run to avoid an O(N) query per message."""
    applied: defaultdict[int, set[int]] = defaultdict(set)
    for row in AppliedTag.objects.filter(evaluation_result__run=run).values("evaluation_result__message_id", "tag_id"):
        applied[row["evaluation_result__message_id"]].add(row["tag_id"])
    return applied


def _compute_stale_by_target(
    run: EvaluationRun,
    possible_tags: frozenset[int],
    applied_by_message: defaultdict[int, set[int]],
    representative_evaluator: Evaluator,
) -> tuple[defaultdict[int, set[int]], ContentType | None]:
    content_type = None
    stale_by_target: defaultdict[int, set[int]] = defaultdict(set)
    messages_qs = run.scoped_messages if run.type == EvaluationRunType.DELTA else run.config.dataset.messages
    for message in messages_qs.select_related("session__chat", "expected_output_chat_message"):
        target = resolve_target(representative_evaluator, message)
        if target is None:
            continue
        if content_type is None:
            content_type = ContentType.objects.get_for_model(type(target))
        stale_tags = possible_tags - applied_by_message[message.pk]
        if stale_tags:
            stale_by_target[target.pk] |= stale_tags
    return stale_by_target, content_type


def reverse_stale_tags(run: EvaluationRun) -> None:
    """Remove stale eval-driven tags after a run completes.

    For each message evaluated in the run, any tag managed by the run's evaluators
    but not applied in this run is removed from the resolved target object.
    PREVIEW runs are skipped entirely.

    Note: not wrapped in transaction.atomic(). A failure mid-loop may leave some
    stale tags in place; a subsequent rerun will complete the cleanup.
    """
    if run.type == EvaluationRunType.PREVIEW:
        return

    evaluators = list(run.config.evaluators.prefetch_related("tag_rules").all())
    possible_tags = _get_possible_tags(evaluators)
    if not possible_tags:
        return

    # All evaluators in a config share the dataset's evaluation_mode (enforced by
    # form validation). Use the first as a representative to carry mode into resolve_target.
    representative_evaluator = evaluators[0]
    applied_by_message = _build_applied_by_message(run)
    stale_by_target, content_type = _compute_stale_by_target(
        run, possible_tags, applied_by_message, representative_evaluator
    )

    if not stale_by_target or content_type is None:
        return

    filter_q = Q()
    for target_pk, tag_ids in stale_by_target.items():
        filter_q |= Q(object_id=target_pk, tag_id__in=tag_ids)

    CustomTaggedItem.objects.filter(content_type=content_type).filter(filter_q).delete()


def undo_run_tags(run: EvaluationRun) -> None:
    """Undo the tag changes applied by this run.

    For each message in this run's scope:
    - removes every managed tag the current run applied to the target
      (tags found in the run's AppliedTag audit records)
    - re-applies every managed tag the previous completed run applied
      to the same target

    PREVIEW runs are skipped entirely.
    If there is no previous run, the current run's tags are removed with
    nothing put back.

    Note: AppliedTag audit rows are never deleted — they remain as history.
    Only CustomTaggedItem (live tag state) is mutated.
    """
    if run.type == EvaluationRunType.PREVIEW:
        return

    evaluators = list(run.config.evaluators.prefetch_related("tag_rules").all())
    possible_tags = _get_possible_tags(evaluators)
    if not possible_tags:
        return

    # All evaluators in a config share the dataset's evaluation_mode.
    # Use the first as a representative to carry mode into resolve_target.
    representative_evaluator = evaluators[0]

    previous_run = (
        EvaluationRun.objects.filter(
            config=run.config,
            status=EvaluationRunStatus.COMPLETED,
            type__in=[EvaluationRunType.FULL, EvaluationRunType.DELTA],
            created_at__lt=run.created_at,
        )
        .order_by("-created_at")
        .first()
    )

    current_applied = _build_applied_by_message(run)
    previous_applied = _build_applied_by_message(previous_run) if previous_run else defaultdict(set)

    content_type = None
    remove_by_target: defaultdict[int, set[int]] = defaultdict(set)
    add_by_target: defaultdict[int, set[int]] = defaultdict(set)

    messages_qs = run.scoped_messages if run.type == EvaluationRunType.DELTA else run.config.dataset.messages
    for message in messages_qs.select_related("session__chat", "expected_output_chat_message"):
        target = resolve_target(representative_evaluator, message)
        if target is None:
            continue
        if content_type is None:
            content_type = ContentType.objects.get_for_model(type(target))
        remove_by_target[target.pk] |= current_applied[message.pk] & possible_tags
        add_by_target[target.pk] |= previous_applied[message.pk] & possible_tags

    if content_type is None:
        return

    if remove_by_target:
        remove_q = Q()
        for target_pk, tag_ids in remove_by_target.items():
            remove_q |= Q(object_id=target_pk, tag_id__in=tag_ids)
        CustomTaggedItem.objects.filter(content_type=content_type).filter(remove_q).delete()

    if add_by_target:
        CustomTaggedItem.objects.bulk_create(
            [
                CustomTaggedItem(
                    content_type=content_type,
                    object_id=target_pk,
                    tag_id=tag_id,
                    team=run.team,
                )
                for target_pk, tag_ids in add_by_target.items()
                for tag_id in tag_ids
            ],
            ignore_conflicts=True,
        )
