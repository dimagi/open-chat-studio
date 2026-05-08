from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.http import QueryDict
from django.utils import timezone
from taskbadger.celery import Task as TaskbadgerTask

from apps.evaluations.models import (
    AutoPopulationRunStatus,
    DatasetAutoPopulationRule,
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMode,
    EvaluationRunType,
)
from apps.evaluations.notifications import auto_population_rule_disabled_notification
from apps.evaluations.utils import make_session_evaluation_messages
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.web.dynamic_filters.datastructures import FilterParams

logger = get_task_logger("ocs.evaluations")


def _versions_of(experiment):
    """Return a queryset including this experiment and all of its versions."""
    base_id = experiment.working_version_id or experiment.id
    return type(experiment).objects.filter(Q(id=base_id) | Q(working_version_id=base_id))


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


def _ingest_rule_session_mode(rule: DatasetAutoPopulationRule, created_floor) -> list[EvaluationMessage]:
    qs = ExperimentSession.objects.filter(
        team=rule.team,
        experiment__in=_versions_of(rule.source_experiment),
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


def _ingest_rule_message_mode(rule: DatasetAutoPopulationRule, created_floor) -> list[EvaluationMessage]:
    base_qs = ExperimentSession.objects.filter(
        team=rule.team,
        experiment__in=_versions_of(rule.source_experiment),
        created_at__gt=created_floor,
    )
    session_external_ids = list(base_qs.values_list("external_id", flat=True))
    if not session_external_ids:
        return []

    # `EvaluationMessage.create_from_sessions` has two independent branches:
    # `external_session_ids` (no filter) and `filtered_session_ids` + `filter_params`
    # (with filter). Pick the right one based on whether the rule has a filter.
    if rule.filter_query_string:
        eval_messages = EvaluationMessage.create_from_sessions(
            team=rule.team,
            external_session_ids=None,
            filtered_session_ids=session_external_ids,
            filter_params=FilterParams(QueryDict(rule.filter_query_string)),
            timezone=None,
        )
    else:
        eval_messages = EvaluationMessage.create_from_sessions(
            team=rule.team,
            external_session_ids=session_external_ids,
        )

    if not eval_messages:
        return []

    existing_pairs = set(
        rule.dataset.messages.filter(
            input_chat_message_id__isnull=False,
            expected_output_chat_message_id__isnull=False,
        ).values_list("input_chat_message_id", "expected_output_chat_message_id")
    )
    fresh = [
        m for m in eval_messages if (m.input_chat_message_id, m.expected_output_chat_message_id) not in existing_pairs
    ]
    if not fresh:
        return []
    created = EvaluationMessage.objects.bulk_create(fresh)
    rule.dataset.messages.add(*created)
    return list(created)


def _ingest_rule(rule: DatasetAutoPopulationRule) -> list[EvaluationMessage]:
    """Scan the rule's source experiment for new sessions, append matches to the dataset.

    Returns the list of newly appended EvaluationMessage rows.
    """
    dataset = rule.dataset
    lookback_floor = timezone.now() - timedelta(days=settings.EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS)
    created_floor = max(rule.created_at, lookback_floor)

    if dataset.evaluation_mode == EvaluationMode.SESSION:
        appended = _ingest_rule_session_mode(rule, created_floor)
    else:
        appended = _ingest_rule_message_mode(rule, created_floor)

    rule.last_run_at = timezone.now()
    update_fields = ["last_run_at", "last_run_status"]
    if appended:
        rule.last_run_status = AutoPopulationRunStatus.SUCCESS
        rule.consecutive_failure_count = 0
        rule.last_error = ""
        update_fields += ["consecutive_failure_count", "last_error"]
    else:
        rule.last_run_status = AutoPopulationRunStatus.NO_OP
    rule.save(update_fields=update_fields)

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
        try:
            with transaction.atomic():
                rule = (
                    DatasetAutoPopulationRule.objects.select_for_update(skip_locked=True)
                    .filter(id=rule_id, is_enabled=True)
                    .first()
                )
                if rule is None:
                    continue  # locked by another worker, or disabled mid-tick
                try:
                    _ingest_rule(rule)
                except Exception as e:  # noqa: BLE001 - per-rule isolation is the point
                    logger.exception("Auto-population rule %s failed: %s", rule.id, e)
                    _handle_rule_failure(rule, e)
        except Exception:  # noqa: BLE001 - last-resort guard
            logger.exception("Unexpected outer error processing auto-population rule %s", rule_id)
