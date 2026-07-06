import csv
import io
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.evaluations.evaluators import EvaluatorResult
from apps.evaluations.models import EvaluationRun, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tasks import export_evaluation_bulk_results_task
from apps.files.models import File, FilePurpose
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)


def _evaluator_output(score: float, generated_response: str) -> dict:
    return EvaluatorResult(
        message={
            "input": {"content": "What is AI?", "role": "human"},
            "output": {"content": "Artificial Intelligence", "role": "ai"},
            "context": {},
            "history": [],
            "metadata": {},
        },
        result={"score": score},
        generated_response=generated_response,
    ).model_dump()


def _read_csv_rows(file_id: int) -> list[dict]:
    content = File.objects.get(id=file_id).file.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(content)))


@pytest.mark.django_db()
def test_export_evaluation_bulk_results_task_creates_csv_file():
    config = EvaluationConfigFactory.create()
    team = config.team
    evaluator = EvaluatorFactory.create(team=team)
    run = EvaluationRunFactory.create(
        team=team, config=config, status=EvaluationRunStatus.COMPLETED, type=EvaluationRunType.FULL
    )
    EvaluationResultFactory.create(
        team=team, run=run, evaluator=evaluator, output=_evaluator_output(8.5, "Generated response")
    )

    result = export_evaluation_bulk_results_task(config.id, team.id)

    file = File.objects.get(id=result["file_id"])
    assert file.purpose == FilePurpose.DATA_EXPORT
    assert file.expiry_date is not None

    rows = _read_csv_rows(result["file_id"])
    assert len(rows) == 1
    assert rows[0][f"score ({evaluator.name})"] == "8.5"
    assert rows[0]["Generated Response"] == "Generated response"


@pytest.mark.django_db()
def test_export_uses_latest_run_result_per_message():
    """When a message has results across multiple completed runs, the latest run wins."""
    config = EvaluationConfigFactory.create()
    team = config.team
    evaluator = EvaluatorFactory.create(team=team)
    message = EvaluationMessageFactory.create()

    old_run = EvaluationRunFactory.create(
        team=team, config=config, status=EvaluationRunStatus.COMPLETED, type=EvaluationRunType.FULL
    )
    # created_at is auto_now_add, so force the ordering via an update that bypasses it.
    EvaluationRun.objects.filter(id=old_run.id).update(created_at=timezone.now() - timedelta(days=1))
    EvaluationResultFactory.create(
        team=team, run=old_run, evaluator=evaluator, message=message, output=_evaluator_output(1.0, "old")
    )

    new_run = EvaluationRunFactory.create(
        team=team, config=config, status=EvaluationRunStatus.COMPLETED, type=EvaluationRunType.FULL
    )
    EvaluationResultFactory.create(
        team=team, run=new_run, evaluator=evaluator, message=message, output=_evaluator_output(9.0, "new")
    )

    result = export_evaluation_bulk_results_task(config.id, team.id)

    rows = _read_csv_rows(result["file_id"])
    assert len(rows) == 1
    assert rows[0][f"score ({evaluator.name})"] == "9.0"
    assert rows[0]["Generated Response"] == "new"
