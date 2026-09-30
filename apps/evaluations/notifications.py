import logging

from apps.evaluations.const import FAILED_FILTER_PARAM
from apps.evaluations.models import DatasetAutoPopulationRule, EvaluationRun, EvaluationRunStatus, EvaluationRunType
from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification
from apps.utils.decorators import silence_exceptions

logger = logging.getLogger("ocs.evaluations")


@silence_exceptions(logger, log_message="Failed to create auto-population disable notification")
def auto_population_rule_disabled_notification(rule: DatasetAutoPopulationRule, reason: str) -> None:
    """Notify team admins that an auto-population rule has been disabled."""
    create_notification(
        title="Auto-population rule disabled",
        message=(
            f"The auto-population rule for dataset '{rule.dataset.name}' "
            f"(source: {rule.source_experiment.name}) was automatically disabled: {reason}."
        ),
        level=LevelChoices.WARNING,
        team=rule.team,
        slug="evaluations-auto-population-disabled",
        event_data={"rule_id": rule.id, "dataset_id": rule.dataset_id},
        permissions=["evaluations.change_evaluationdataset"],
        links={"View dataset": rule.dataset.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create evaluation run outcome notification")
def evaluation_run_outcome_notification(run: EvaluationRun) -> None:
    """Tell the team when a completed run has enough failed results to mislead."""
    if run.status != EvaluationRunStatus.COMPLETED:
        return
    # A preview is watched as it runs, and its page already shows the same warning.
    if run.type == EvaluationRunType.PREVIEW:
        return
    summary = run.error_summary()
    if not summary.needs_warning:
        return

    create_notification(
        title=f"Evaluation '{run.config.name}' had failed results",
        message=(
            f"{summary.failed_count} of {summary.total_count} messages in the evaluation run failed "
            f"({summary.describe()}). Failed results are left out of the aggregates, tags and trends."
        ),
        level=LevelChoices.WARNING,
        team=run.team,
        slug="evaluation-run-outcome",
        event_data={"evaluation_run_id": run.id},
        permissions=["evaluations.view_evaluationrun"],
        links={"View failed results": f"{run.get_absolute_url()}?{FAILED_FILTER_PARAM}=1"},
        once_per_event_type=True,
    )
