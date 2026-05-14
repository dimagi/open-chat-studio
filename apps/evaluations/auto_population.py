from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import QueryDict
from django.utils import timezone
from taskbadger.celery import Task as TaskbadgerTask

from apps.evaluations.models import (
    AutoPopulationRunStatus,
    DatasetAutoPopulationRule,
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationRunType,
)
from apps.evaluations.notifications import auto_population_rule_disabled_notification
from apps.evaluations.utils import make_session_evaluation_messages
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.web.dynamic_filters.datastructures import FilterParams

logger = get_task_logger("ocs.evaluations")


def _trigger_delta_runs_for_dataset(dataset: EvaluationDataset, appended: list[EvaluationMessage]) -> None:
    """Enqueue a DELTA evaluation run for each opted-in config on this dataset."""
    configs = EvaluationConfig.objects.filter(dataset=dataset, auto_run_on_append=True)
    for config in configs:
        config.run(run_type=EvaluationRunType.DELTA, scoped_messages=appended)


def _handle_rule_failure(rule: DatasetAutoPopulationRule, exception: Exception) -> None:
    """Record a failure on the rule; auto-disable after the configured threshold."""
    rule.consecutive_failure_count = (rule.consecutive_failure_count or 0) + 1
    rule.last_run_status = AutoPopulationRunStatus.ERROR
    rule.last_run_at = timezone.now()
    rule.last_error = str(exception)[:1000]

    update_fields = [
        "consecutive_failure_count",
        "last_run_status",
        "last_run_at",
        "last_error",
    ]
    if rule.consecutive_failure_count >= DatasetAutoPopulationRule.AUTO_DISABLE_FAILURE_THRESHOLD:
        rule.is_enabled = False
        update_fields.append("is_enabled")

    rule.save(update_fields=update_fields)

    if not rule.is_enabled:
        auto_population_rule_disabled_notification(
            rule, reason=f"{rule.consecutive_failure_count} consecutive failures"
        )


def _scan_for_new_sessions(rule: DatasetAutoPopulationRule, created_floor) -> list[EvaluationMessage]:
    """Find new sessions matching the rule's filter and build session-mode EvaluationMessages."""
    qs = ExperimentSession.objects.filter(
        team=rule.team,
        experiment=rule.source_experiment,
        created_at__gt=created_floor,
    ).exclude(id__in=rule.dataset.messages.filter(session__isnull=False).values_list("session_id", flat=True))

    if rule.filter_query_string:
        params = FilterParams(QueryDict(rule.filter_query_string))
        qs = ExperimentSessionFilter().apply(qs, params, timezone=None)

    session_external_ids = list(qs.values_list("external_id", flat=True))
    if not session_external_ids:
        return []

    eval_messages = make_session_evaluation_messages(session_external_ids, team=rule.team)
    if not eval_messages:
        return []
    created = EvaluationMessage.objects.bulk_create(eval_messages)
    rule.dataset.messages.add(*created)
    return list(created)


def _ingest_rule(rule: DatasetAutoPopulationRule) -> list[EvaluationMessage]:
    """Scan the rule's source experiment for new sessions, append matches to the dataset.

    Rules are only valid against session-mode datasets (enforced by
    `DatasetAutoPopulationRule.clean()`), so the scan always builds
    session-mode `EvaluationMessage` rows.

    Returns the list of newly appended EvaluationMessage rows.
    """
    dataset = rule.dataset
    lookback_floor = timezone.now() - timedelta(days=settings.EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS)
    created_floor = max(rule.created_at, lookback_floor)

    appended = _scan_for_new_sessions(rule, created_floor)

    # SUCCESS and NO_OP are both healthy tick outcomes — neither should let a
    # previous failure linger and contribute to auto-disable. Reset the failure
    # counter and clear last_error in both cases.
    rule.last_run_at = timezone.now()
    rule.last_run_status = AutoPopulationRunStatus.SUCCESS if appended else AutoPopulationRunStatus.NO_OP
    rule.consecutive_failure_count = 0
    rule.last_error = ""
    rule.save(update_fields=["last_run_at", "last_run_status", "consecutive_failure_count", "last_error"])

    if appended:
        transaction.on_commit(lambda: _trigger_delta_runs_for_dataset(dataset, appended))
    return appended


@shared_task(base=TaskbadgerTask)
def auto_populate_eval_datasets():
    """Periodic task: walk enabled DatasetAutoPopulationRules and ingest matches.

    Each rule is processed inside its own transaction with a row-level lock
    (`select_for_update(skip_locked=True)`) so two beat workers cannot
    double-process the same rule. A failure on one rule never blocks others.
    """
    rule_ids = list(
        DatasetAutoPopulationRule.objects.filter(is_enabled=True)
        .order_by(F("last_run_at").asc(nulls_first=True))
        .values_list("id", flat=True)
    )
    for rule_id in rule_ids:
        failure_exception = None
        try:
            with transaction.atomic():
                rule = (
                    DatasetAutoPopulationRule.objects.select_for_update(skip_locked=True)
                    .filter(id=rule_id, is_enabled=True)
                    .first()
                )
                if rule is None:
                    continue  # locked by another worker, or disabled mid-tick
                _ingest_rule(rule)
        except Exception as e:  # noqa: BLE001 - per-rule isolation is the point
            logger.exception("Auto-population rule %s failed: %s", rule_id, e)
            failure_exception = e

        if failure_exception is None:
            continue

        # Record the failure in a fresh transaction — the original `transaction.atomic()`
        # block may have been aborted by a database error, so `_handle_rule_failure`'s
        # `rule.save()` must run outside of it. Re-fetch the rule since the locked
        # instance is unusable after rollback.
        try:
            rule = DatasetAutoPopulationRule.objects.filter(id=rule_id).first()
            if rule is not None:
                _handle_rule_failure(rule, failure_exception)
        except Exception:  # noqa: BLE001 - last-resort guard
            logger.exception("Failed to record failure state for auto-population rule %s", rule_id)
