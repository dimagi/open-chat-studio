"""Test helper for driving evaluation coordination synchronously."""

from unittest.mock import patch

from apps.evaluations.models import NON_TERMINAL_RUN_STATUSES, EvaluationRun
from apps.evaluations.tasks import drive_evaluation_run, finalize_evaluation_run


def sweep():
    """Tick every active run synchronously, as a worker would.

    `coordinate_evaluation_runs` only fans out; the per-run tick and the completion side
    effects are separate tasks (`drive_evaluation_run`, `finalize_evaluation_run`), so both
    are run inline here to leave the same end state a worker would reach.
    """
    run_ids = list(EvaluationRun.objects.filter(status__in=NON_TERMINAL_RUN_STATUSES).values_list("id", flat=True))
    with patch("apps.evaluations.tasks.finalize_evaluation_run.delay", side_effect=finalize_evaluation_run):
        for run_id in run_ids:
            drive_evaluation_run(run_id)
