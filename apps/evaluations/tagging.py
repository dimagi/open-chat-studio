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


def resolve_target(evaluation_mode: str, evaluation_message: EvaluationMessage) -> Chat | ChatMessage | None:
    """Return the object to tag for the given evaluation mode, or None if no target.

    SESSION mode returns the session's `Chat` (not the `ExperimentSession` itself) because
    `Chat` owns the TaggedModelMixin contract — tags live on the chat, not the session row.
    MESSAGE mode tags the expected-output `ChatMessage`.
    """
    if evaluation_mode == EvaluationMode.SESSION:
        session = evaluation_message.session
        if session is None:
            return None
        return session.chat
    return evaluation_message.expected_output_chat_message


def _content_type_for_mode(evaluation_mode: str) -> ContentType:
    """ContentType of the object a mode tags: `Chat` for SESSION, `ChatMessage` otherwise.

    Mirrors `resolve_target`'s target choice, letting callers derive the content type from
    the mode alone instead of from a resolved instance.
    """
    from apps.chat.models import Chat, ChatMessage  # noqa: PLC0415 — avoid circular import

    model = Chat if evaluation_mode == EvaluationMode.SESSION else ChatMessage
    return ContentType.objects.get_for_model(model)


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

    target = resolve_target(evaluator.evaluation_mode, evaluation_message)
    if target is None:
        return

    matched_rules = evaluate_rules(rules, evaluation_result.output or {})
    if not matched_rules:
        return

    tags_to_apply = {rule.tag_id: rule.tag for rule in matched_rules}
    content_type = _content_type_for_mode(evaluator.evaluation_mode)
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


def _applied_by_message_for_run_ids(run_ids: list[int]) -> dict[int, set[int]]:
    """Map message_id -> {tag_id} across the AppliedTag rows of the given runs.

    One query for the whole run set. Because DELTA message-sets are disjoint and a FULL
    run re-covers everything (the DELTA invariant), a run set of {previous FULL + its
    DELTAs} contributes at most one tag-set per message, so no per-message run resolution
    is needed.
    """
    applied = defaultdict(set)
    if not run_ids:
        return applied
    for row in AppliedTag.objects.filter(evaluation_result__run_id__in=run_ids).values(
        "evaluation_result__message_id", "tag_id"
    ):
        applied[row["evaluation_result__message_id"]].add(row["tag_id"])
    return applied


def _compute_stale_by_target(
    run: EvaluationRun,
    possible_tags: frozenset[int],
    applied_by_message: dict[int, set[int]],
    evaluation_mode: str,
) -> defaultdict[int, set[int]]:
    stale_by_target: defaultdict[int, set[int]] = defaultdict(set)
    messages_qs = run.scoped_messages if run.type == EvaluationRunType.DELTA else run.config.dataset.messages
    for message in messages_qs.select_related("session__chat", "expected_output_chat_message"):
        target = resolve_target(evaluation_mode, message)
        if target is None:
            continue
        stale_tags = possible_tags - applied_by_message[message.pk]
        if stale_tags:
            stale_by_target[target.pk] |= stale_tags
    return stale_by_target


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

    # All evaluators in a config share the dataset's evaluation_mode (enforced by form
    # validation), so the first evaluator's mode determines every target and content type.
    evaluation_mode = evaluators[0].evaluation_mode
    applied_by_message = _applied_by_message_for_run_ids([run.pk])
    stale_by_target = _compute_stale_by_target(run, possible_tags, applied_by_message, evaluation_mode)

    if not stale_by_target:
        return

    content_type = _content_type_for_mode(evaluation_mode)
    filter_q = Q()
    for target_pk, tag_ids in stale_by_target.items():
        filter_q |= Q(object_id=target_pk, tag_id__in=tag_ids)

    CustomTaggedItem.objects.filter(content_type=content_type).filter(filter_q).delete()


def archive_superseded_runs(run: EvaluationRun) -> None:
    """Mark the runs this run supersedes as ``tags_archived=True``.

    The AppliedTag rows of non-archived runs mirror the live tag state. Only a FULL run
    supersedes anything: it re-evaluates the whole dataset, so every other currently-active
    run of the config (the previous FULL and its DELTAs) is superseded. A DELTA run only
    adds tags for disjoint, brand-new sessions (the DELTA invariant), so it supersedes
    nothing. PREVIEW runs never apply tags. Called at run completion.
    """
    if run.type != EvaluationRunType.FULL:
        return

    EvaluationRun.objects.filter(config=run.config, tags_archived=False).exclude(pk=run.pk).update(tags_archived=True)


def _restore_set_for_run(run: EvaluationRun) -> list[EvaluationRun]:
    """The runs whose tags become live again when `run` (a FULL run) is undone.

    A FULL run supersedes the previous tagging epoch: the most recent FULL run that
    finished before it (``P``) plus every DELTA that finished between ``P`` and this run.
    Undoing `run` restores exactly that set. When there is no earlier FULL run, the epoch
    is just the DELTAs that finished before this run (the first FULL re-covered their
    new sessions, so they revert to their own tags).

    Ordering uses ``finished_at`` (with ``-id`` as a stable tie-breaker), consistent with
    ``_latest_completed_full_run``: runs for a config can overlap with no concurrency guard,
    so completion time — not creation time — reflects which prior state is most recent.
    """
    completed = EvaluationRun.objects.filter(
        config=run.config,
        status=EvaluationRunStatus.COMPLETED,
        finished_at__lt=run.finished_at,
    )
    prev_full = completed.filter(type=EvaluationRunType.FULL).order_by("-finished_at", "-id").first()

    deltas = completed.filter(type=EvaluationRunType.DELTA)
    if prev_full is not None:
        deltas = deltas.filter(finished_at__gt=prev_full.finished_at)

    restore = list(deltas)
    if prev_full is not None:
        restore.append(prev_full)
    return restore


def _compute_undo_target_diffs(
    run: EvaluationRun,
    restore_runs: list[EvaluationRun],
    possible_tags: frozenset[int],
    evaluation_mode: str,
) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Compute the (remove, add) managed-tag deltas per target for undoing `run`.

    `remove` is tags `run` applied that the restore set does not have; `add` is tags the
    restore set applied that `run` does not have. Both are intersected with `possible_tags`
    so we never touch tags this config doesn't manage. The restore set's AppliedTag rows
    cover disjoint messages (the DELTA invariant), so they compose without per-message run
    resolution. Returns (remove_by_target, add_by_target); both are empty when no message
    resolved to a target.
    """
    current_applied = _applied_by_message_for_run_ids([run.pk])
    restore_applied = _applied_by_message_for_run_ids([r.pk for r in restore_runs])

    # Every message either run touched: undone-run messages (remove) and restore-set
    # messages (add). Their union is the full set of targets undo must reconcile.
    message_ids = set(current_applied) | set(restore_applied)

    remove_by_target: defaultdict[int, set[int]] = defaultdict(set)
    add_by_target: defaultdict[int, set[int]] = defaultdict(set)

    messages_iter = EvaluationMessage.objects.filter(pk__in=message_ids).select_related(
        "session__chat", "expected_output_chat_message"
    )
    for message in messages_iter:
        target = resolve_target(evaluation_mode, message)
        if target is None:
            continue
        # Diff current vs restore so we only touch tags that actually change:
        # drop tags this run added that the restored epoch didn't have, and restore
        # epoch tags this run no longer has. Tags common to both are left alone.
        current_tags = current_applied[message.pk] & possible_tags
        restore_tags = restore_applied[message.pk] & possible_tags
        remove_by_target[target.pk] |= current_tags - restore_tags
        add_by_target[target.pk] |= restore_tags - current_tags

    return remove_by_target, add_by_target


def _apply_undo_target_diffs(
    team,
    content_type: ContentType,
    remove_by_target: dict[int, set[int]],
    add_by_target: dict[int, set[int]],
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


def _update_archive_flags_for_undo(run: EvaluationRun, restore_runs: list[EvaluationRun]) -> None:
    """Flip ``tags_archived`` to reflect the post-undo live state.

    This run's tags become archived (no longer live); the restored epoch — the previous
    FULL run plus its DELTAs — becomes active again. Keeps the "non-archived runs mirror
    live tags" invariant true after an undo. Must run in the same transaction as the
    CustomTaggedItem mutation.
    """
    EvaluationRun.objects.filter(pk=run.pk).update(tags_archived=True)
    if restore_runs:
        EvaluationRun.objects.filter(pk__in=[r.pk for r in restore_runs]).update(tags_archived=False)


def undo_run_tags(run: EvaluationRun) -> None:
    """Undo the tag changes applied by this FULL run, restoring the previous epoch.

    Undo reverts the live tags to the state immediately before `run`: the previous FULL
    run plus every DELTA that ran between it and `run` (the "restore set"). Per resolved
    target, tags `run` added that the restore set lacks are removed, and tags the restore
    set had that `run` dropped are re-applied. Because DELTA message-sets are disjoint and
    a FULL run re-covers everything (the DELTA invariant), the restore set composes into
    one coherent prior state with no per-message run resolution.

    Only FULL runs are undoable; DELTA and PREVIEW runs are no-ops here (eligibility is
    enforced by ``can_undo_tags``). AppliedTag audit rows are never deleted — only
    ``EvaluationRun.tags_archived`` flips (this run archived, the restore set reactivated)
    and CustomTaggedItem (live tag state) is mutated. Both share a single
    transaction.atomic() block so a partial failure rolls back.
    """
    if run.type != EvaluationRunType.FULL:
        return

    evaluators = list(run.config.evaluators.prefetch_related("tag_rules").all())
    possible_tags = _get_possible_tags(evaluators)
    if not possible_tags:
        return

    # All evaluators in a config share the dataset's evaluation_mode, so the first
    # evaluator's mode determines every target and content type.
    evaluation_mode = evaluators[0].evaluation_mode

    restore_runs = _restore_set_for_run(run)
    remove_by_target, add_by_target = _compute_undo_target_diffs(run, restore_runs, possible_tags, evaluation_mode)

    with transaction.atomic():
        if remove_by_target or add_by_target:
            content_type = _content_type_for_mode(evaluation_mode)
            _apply_undo_target_diffs(run.team, content_type, remove_by_target, add_by_target)
        _update_archive_flags_for_undo(run, restore_runs)


def _latest_completed_full_run(config) -> EvaluationRun | None:
    """The most recently finished COMPLETED FULL run for the config (``-finished_at, -id`` order)."""
    return (
        EvaluationRun.objects.filter(
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        )
        .order_by("-finished_at", "-id")
        .first()
    )


def can_undo_tags(run: EvaluationRun) -> bool:
    """Whether this run's tags may be undone.

    Only the latest completed FULL run for its config is undoable, and only once: once its
    tags have been archived (by a prior undo) it can no longer be undone. DELTA/PREVIEW runs
    are never directly undoable — undoing the trailing FULL run restores their tags instead.
    """
    if run.status != EvaluationRunStatus.COMPLETED or run.type != EvaluationRunType.FULL:
        return False

    latest = _latest_completed_full_run(run.config)
    if latest is None or latest.pk != run.pk:
        return False

    return not run.tags_archived
