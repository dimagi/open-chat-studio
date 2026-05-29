"""Eval-driven tagging: DB orchestration.

Pure validators live in `rule_validation.py`. The DB-touching orchestrator
at the bottom is called from the evaluation task.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q

from apps.annotations.models import CustomTaggedItem
from apps.evaluations.models import (
    AppliedTag,
    ConditionType,
    EvaluationMessage,
    EvaluationMode,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
)

if TYPE_CHECKING:
    from apps.chat.models import Chat, ChatMessage
    from apps.evaluations.models import (
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


def _message_ids_by_latest_prior_run(run: EvaluationRun, message_ids: list[int]) -> defaultdict[int, set[int]]:
    """Identify the latest prior FULL/DELTA run of the same config that evaluated each
    message in `message_ids`, and return a mapping `{run_id: {message_id, ...}}` grouping
    messages by their shared previous-state run.

    Only runs that are COMPLETED, of the same config, and strictly older than `run` are
    considered. Postgres `DISTINCT ON (message_id)` does the per-message dedup in the DB.
    Extracted from `_previous_applied_by_message` so it can be tested in isolation.
    """
    qs = (
        EvaluationResult.objects.filter(
            run__config=run.config,
            run__status=EvaluationRunStatus.COMPLETED,
            run__type__in=[EvaluationRunType.FULL, EvaluationRunType.DELTA],
            run__created_at__lt=run.created_at,
            message_id__in=message_ids,
        )
        .order_by("message_id", "-run__created_at")
        .distinct("message_id")
        .values_list("message_id", "run_id")
    )
    runs_to_message_ids: defaultdict[int, set[int]] = defaultdict(set)
    for message_id, run_id in qs.iterator():
        runs_to_message_ids[run_id].add(message_id)
    return runs_to_message_ids


def _previous_applied_by_message(run: EvaluationRun, message_ids: list[int]) -> defaultdict[int, set[int]]:
    """For each message_id, return tags applied by the most recent *prior* run that evaluated it.

    A DELTA run that did not touch a given message is skipped per-message: the walk continues
    back through prior FULL/DELTA runs until one is found whose EvaluationResult set
    includes that message. That run's AppliedTag rows for the message form the
    "previous state" we restore.

    A prior run that evaluated the message but produced no tag fires is treated as
    "previous state = no managed tags" — which is what we want, since `reverse_stale_tags`
    would have wiped any older tags at that point.

    Returns a defaultdict(set) keyed by message_id.
    """
    if not message_ids:
        return defaultdict(set)

    runs_to_message_ids = _message_ids_by_latest_prior_run(run, message_ids)
    if not runs_to_message_ids:
        return defaultdict(set)

    # One AppliedTag query for all (run, messages) groups, instead of one per run.
    filter_q = Q()
    for run_id, msg_id_set in runs_to_message_ids.items():
        filter_q |= Q(evaluation_result__run_id=run_id, evaluation_result__message_id__in=msg_id_set)

    applied: defaultdict[int, set[int]] = defaultdict(set)
    rows = AppliedTag.objects.filter(filter_q).values_list("evaluation_result__message_id", "tag_id")
    for message_id, tag_id in rows:
        applied[message_id].add(tag_id)

    return applied


def _compute_undo_target_diffs(
    run: EvaluationRun,
    evaluated_message_ids: list[int],
    possible_tags: frozenset[int],
    representative_evaluator: Evaluator,
) -> tuple[defaultdict[int, set[int]], defaultdict[int, set[int]], ContentType | None]:
    """For each evaluated message, compute the (remove, add) managed-tag deltas per target.

    `remove` is tags this run's AppliedTag rows applied; `add` is tags the most recent
    prior run had applied. Both are intersected with `possible_tags` so we never touch
    tags this config doesn't manage. Returns (remove_by_target, add_by_target, content_type);
    content_type is None when no message resolved to a target.
    """
    current_applied = _build_applied_by_message(run)
    previous_applied = _previous_applied_by_message(run, evaluated_message_ids)

    content_type: ContentType | None = None
    remove_by_target: defaultdict[int, set[int]] = defaultdict(set)
    add_by_target: defaultdict[int, set[int]] = defaultdict(set)

    messages_iter = EvaluationMessage.objects.filter(pk__in=evaluated_message_ids).select_related(
        "session__chat", "expected_output_chat_message"
    )
    for message in messages_iter:
        target = resolve_target(representative_evaluator, message)
        if target is None:
            continue
        if content_type is None:
            content_type = ContentType.objects.get_for_model(type(target))
        remove_by_target[target.pk] |= current_applied[message.pk] & possible_tags
        add_by_target[target.pk] |= previous_applied[message.pk] & possible_tags

    return remove_by_target, add_by_target, content_type


def _apply_undo_target_diffs(
    team,
    content_type: ContentType,
    remove_by_target: defaultdict[int, set[int]],
    add_by_target: defaultdict[int, set[int]],
) -> None:
    """Apply the per-target tag mutations atomically: delete removes, then bulk_create adds."""
    with transaction.atomic():
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
                        team=team,
                    )
                    for target_pk, tag_ids in add_by_target.items()
                    for tag_id in tag_ids
                ],
                ignore_conflicts=True,
            )


def undo_run_tags(run: EvaluationRun) -> None:
    """Undo the tag changes applied by this run.

    For each message this run evaluated:
    - removes every managed tag the current run applied to the target
      (tags found in the run's AppliedTag audit records)
    - re-applies every managed tag from the most recent prior completed FULL/DELTA
      run that evaluated that *same message*. The walk is per-message: a DELTA
      predecessor that did not touch a given message is skipped, and we look
      further back until a prior run that did evaluate the message is found.

    PREVIEW runs are skipped entirely.

    The set of messages to process is sourced from this run's EvaluationResult rows,
    not the live dataset — so later edits to the dataset do not affect what undo
    touches.

    AppliedTag audit rows are never deleted — they remain as history. Only
    CustomTaggedItem (live tag state) is mutated. The delete + bulk_create are
    wrapped in a single transaction.atomic() block so a partial failure rolls back.
    """
    if run.type == EvaluationRunType.PREVIEW:
        return

    evaluators = list(run.config.evaluators.prefetch_related("tag_rules").all())
    possible_tags = _get_possible_tags(evaluators)
    if not possible_tags:
        return

    evaluated_message_ids = list(
        EvaluationResult.objects.filter(run=run).values_list("message_id", flat=True).distinct()
    )
    if not evaluated_message_ids:
        return

    # All evaluators in a config share the dataset's evaluation_mode.
    # Use the first as a representative to carry mode into resolve_target.
    representative_evaluator = evaluators[0]

    remove_by_target, add_by_target, content_type = _compute_undo_target_diffs(
        run, evaluated_message_ids, possible_tags, representative_evaluator
    )
    if content_type is None:
        return

    _apply_undo_target_diffs(run.team, content_type, remove_by_target, add_by_target)
