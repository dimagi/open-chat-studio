import csv
import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.evaluations.evaluators import EvaluatorResult
from apps.evaluations.models import (
    EvaluationMessage,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
)
from apps.evaluations.tasks import (
    _count_bulk_export_rows,
    _report_row_progress,
    export_evaluation_bulk_results_task,
)
from apps.files.models import File, FilePurpose
from apps.utils.factories.evaluations import (
    AppliedTagFactory,
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)


@pytest.fixture(autouse=True)
def _stub_progress_recorder():
    """ProgressRecorder needs a live Celery request; these tests call the task directly."""
    with patch("apps.evaluations.tasks.ProgressRecorder"):
        yield


def _completed_run(config=None) -> EvaluationRun:
    """A completed FULL run, the only shape the bulk export picks up."""
    config = config or EvaluationConfigFactory.create()
    return EvaluationRunFactory.create(
        team=config.team, config=config, status=EvaluationRunStatus.COMPLETED, type=EvaluationRunType.FULL
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
    content = File.objects.get(id=file_id).read_bytes().decode("utf-8")
    return list(csv.DictReader(io.StringIO(content)))


@pytest.mark.django_db()
def test_export_evaluation_bulk_results_task_creates_csv_file():
    run = _completed_run()
    evaluator = EvaluatorFactory.create(team=run.team)
    EvaluationResultFactory.create(
        team=run.team, run=run, evaluator=evaluator, output=_evaluator_output(8.5, "Generated response")
    )

    result = export_evaluation_bulk_results_task(run.config_id, run.team_id)

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

    old_run = _completed_run(config)
    # created_at is auto_now_add, so force the ordering via an update that bypasses it.
    EvaluationRun.objects.filter(id=old_run.id).update(created_at=timezone.now() - timedelta(days=1))
    EvaluationResultFactory.create(
        team=team, run=old_run, evaluator=evaluator, message=message, output=_evaluator_output(1.0, "old")
    )

    new_run = _completed_run(config)
    EvaluationResultFactory.create(
        team=team, run=new_run, evaluator=evaluator, message=message, output=_evaluator_output(9.0, "new")
    )

    result = export_evaluation_bulk_results_task(config.id, team.id)

    rows = _read_csv_rows(result["file_id"])
    assert len(rows) == 1
    assert rows[0][f"score ({evaluator.name})"] == "9.0"
    assert rows[0]["Generated Response"] == "new"


@pytest.mark.django_db()
def test_export_includes_applied_tags():
    """Tags reach the CSV without the queryset prefetching them per result."""
    run = _completed_run()
    team = run.team
    evaluator = EvaluatorFactory.create(team=team)
    result = EvaluationResultFactory.create(
        team=team, run=run, evaluator=evaluator, output=_evaluator_output(1.0, "response")
    )
    rule = EvaluatorTagRuleFactory.create(team=team, evaluator=evaluator)
    other_rule = EvaluatorTagRuleFactory.create(team=team, evaluator=evaluator)
    AppliedTagFactory.create(team=team, evaluation_result=result, rule=rule)
    AppliedTagFactory.create(team=team, evaluation_result=result, rule=other_rule)

    rows = _read_csv_rows(export_evaluation_bulk_results_task(run.config_id, team.id)["file_id"])

    assert rows[0]["Applied Tags"] == ", ".join(sorted([rule.tag.name, other_rule.tag.name]))


@pytest.mark.django_db()
def test_export_unions_columns_across_evaluators_with_different_outputs():
    """A column first seen on a late message still gets a header, and earlier rows a blank cell."""
    run = _completed_run()
    team = run.team
    early_evaluator = EvaluatorFactory.create(team=team, name="Early")
    late_evaluator = EvaluatorFactory.create(team=team, name="Late")
    first_message = EvaluationMessageFactory.create()
    second_message = EvaluationMessageFactory.create()
    EvaluationResultFactory.create(
        team=team, run=run, evaluator=early_evaluator, message=first_message, output=_evaluator_output(1.0, "first")
    )
    EvaluationResultFactory.create(
        team=team, run=run, evaluator=late_evaluator, message=second_message, output=_evaluator_output(2.0, "second")
    )

    rows = _read_csv_rows(export_evaluation_bulk_results_task(run.config_id, team.id)["file_id"])

    assert len(rows) == 2
    assert rows[0]["score (Early)"] == "1.0"
    assert rows[0]["score (Late)"] == ""
    assert rows[1]["score (Early)"] == ""
    assert rows[1]["score (Late)"] == "2.0"


@pytest.mark.django_db()
def test_export_combines_every_evaluator_for_one_message_into_one_row():
    run = _completed_run()
    team = run.team
    evaluators = [EvaluatorFactory.create(team=team, name=f"Evaluator {i}") for i in range(3)]
    message = EvaluationMessageFactory.create()
    for index, evaluator in enumerate(evaluators):
        EvaluationResultFactory.create(
            team=team,
            run=run,
            evaluator=evaluator,
            message=message,
            output=_evaluator_output(float(index), f"response {index}"),
        )

    rows = _read_csv_rows(export_evaluation_bulk_results_task(run.config_id, team.id)["file_id"])

    assert len(rows) == 1
    for index, evaluator in enumerate(evaluators):
        assert rows[0][f"score ({evaluator.name})"] == str(float(index))


@pytest.mark.django_db()
def test_export_query_count_does_not_grow_with_result_count():
    """The export streams: a 200-message config must cost the same queries as a 2-message one."""

    def _build(message_count: int) -> EvaluationRun:
        run = _completed_run()
        evaluator = EvaluatorFactory.create(team=run.team)
        messages = EvaluationMessage.objects.bulk_create(
            [
                EvaluationMessage(
                    input={"content": f"in {i}", "role": "human"},
                    output={"content": "out", "role": "ai"},
                )
                for i in range(message_count)
            ]
        )
        EvaluationResult.objects.bulk_create(
            [
                EvaluationResult(
                    team=run.team, run=run, evaluator=evaluator, message=message, output=_evaluator_output(1.0, "r")
                )
                for message in messages
            ]
        )
        return run

    small_run = _build(2)
    large_run = _build(200)

    with CaptureQueriesContext(connection) as small_ctx:
        export_evaluation_bulk_results_task(small_run.config_id, small_run.team_id)
    with CaptureQueriesContext(connection) as large_ctx:
        export_evaluation_bulk_results_task(large_run.config_id, large_run.team_id)

    assert len(large_ctx.captured_queries) == len(small_ctx.captured_queries), (
        f"query count grew from {len(small_ctx.captured_queries)} (2 messages) to "
        f"{len(large_ctx.captured_queries)} (200 messages) - the export is not streaming"
    )


@pytest.mark.django_db()
def test_count_bulk_export_rows_counts_messages_not_results():
    """The progress denominator is CSV rows, which is one per message however many
    evaluators and runs contributed to it."""
    config = EvaluationConfigFactory.create()
    team = config.team
    evaluators = [EvaluatorFactory.create(team=team, name=f"Evaluator {i}") for i in range(2)]
    messages = [EvaluationMessageFactory.create() for _ in range(3)]
    for run in (_completed_run(config), _completed_run(config)):
        for evaluator in evaluators:
            for message in messages:
                EvaluationResultFactory.create(
                    team=team, run=run, evaluator=evaluator, message=message, output=_evaluator_output(1.0, "r")
                )

    assert _count_bulk_export_rows(config, team) == 3


def test_report_row_progress_yields_every_row_but_reports_in_steps():
    """Reporting once per row would be one backend write per row on a large export."""
    rows = [{"#": index} for index in range(250)]
    reported = []

    yielded = list(_report_row_progress(rows, len(rows), lambda current, total: reported.append((current, total))))

    assert yielded == rows
    assert reported[-1] == (250, 250)
    assert len(reported) <= 101


@pytest.mark.django_db()
def test_export_drives_progress_to_completion(monkeypatch):
    """The UI bar is fed by the recorder, so the task must reach total/total."""
    run = _completed_run()
    evaluator = EvaluatorFactory.create(team=run.team)
    for _ in range(3):
        EvaluationResultFactory.create(
            team=run.team,
            run=run,
            evaluator=evaluator,
            message=EvaluationMessageFactory.create(),
            output=_evaluator_output(1.0, "r"),
        )

    calls = []

    class _Recorder:
        def __init__(self, task):
            pass

        def set_progress(self, current, total, description=""):
            calls.append((current, total))

    monkeypatch.setattr("apps.evaluations.tasks.ProgressRecorder", _Recorder)

    result = export_evaluation_bulk_results_task(run.config_id, run.team_id)

    assert "file_id" in result
    assert calls
    assert calls[-1] == (3, 3)
