from unittest.mock import Mock, patch

import pytest
from django.db import IntegrityError

from apps.evaluations.const import PREVIEW_SAMPLE_SIZE
from apps.evaluations.models import EvaluationResult, EvaluationRunType
from apps.evaluations.tasks import evaluate_single_message_task
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_coordination_fields_default_empty():
    run = EvaluationRunFactory.create()
    assert run.in_flight == []
    assert run.evaluator_ids == []
    assert run.wave_dispatched_at is None
    assert run.taskbadger_task_id == ""
    assert run.stall_count == 0


@pytest.mark.django_db()
def test_evaluation_result_unique_per_run_message_evaluator():
    run = EvaluationRunFactory.create()
    evaluator = EvaluatorFactory.create(team=run.team)
    message = EvaluationMessageFactory.create()
    EvaluationResultFactory.create(team=run.team, run=run, evaluator=evaluator, message=message, output={})
    with pytest.raises(IntegrityError):
        EvaluationResultFactory.create(team=run.team, run=run, evaluator=evaluator, message=message, output={})


@pytest.mark.django_db()
def test_run_freezes_full_plan_and_evaluators():
    config = EvaluationConfigFactory.create()
    extra = EvaluationMessageFactory.create()
    config.dataset.messages.add(extra)
    all_ids = set(config.dataset.messages.values_list("id", flat=True))
    evaluator_ids = list(config.evaluators.values_list("id", flat=True))

    with patch("apps.evaluations.tasks.run_evaluation_task.delay"):
        run = config.run(run_type=EvaluationRunType.FULL)

    assert set(run.scoped_messages.values_list("id", flat=True)) == all_ids
    assert run.evaluator_ids == evaluator_ids
    assert run.job_id  # a uuid was assigned


@pytest.mark.django_db()
def test_run_freezes_preview_sample():
    config = EvaluationConfigFactory.create()
    for _ in range(PREVIEW_SAMPLE_SIZE + 5):
        config.dataset.messages.add(EvaluationMessageFactory.create())

    with patch("apps.evaluations.tasks.run_evaluation_task.delay"):
        run = config.run(run_type=EvaluationRunType.PREVIEW)

    assert run.scoped_messages.count() == PREVIEW_SAMPLE_SIZE


@pytest.mark.django_db()
def test_run_freezes_delta_explicit_list():
    config = EvaluationConfigFactory.create()
    msg1 = EvaluationMessageFactory.create()
    msg2 = EvaluationMessageFactory.create()

    with patch("apps.evaluations.tasks.run_evaluation_task.delay"):
        run = config.run(run_type=EvaluationRunType.DELTA, scoped_messages=[msg1, msg2])

    assert set(run.scoped_messages.all()) == {msg1, msg2}


@pytest.fixture()
def coordination_run(db):
    team = TeamWithUsersFactory.create()
    config = EvaluationConfigFactory.create(team=team)
    evaluator = EvaluatorFactory.create(team=team)
    config.evaluators.set([evaluator])
    message = EvaluationMessageFactory.create()
    config.dataset.messages.add(message)
    run = EvaluationRunFactory.create(config=config, team=team, evaluator_ids=[evaluator.id])
    run.scoped_messages.add(message)
    return run, evaluator, message


@pytest.mark.django_db()
@patch("apps.evaluations.models.Evaluator.run")
def test_evaluate_single_message_skips_already_evaluated(evaluator_run_mock, coordination_run):
    run, evaluator, message = coordination_run
    EvaluationResultFactory.create(
        team=run.team, run=run, evaluator=evaluator, message=message, output={"result": {"score": 1}}
    )

    evaluate_single_message_task(run.id, [evaluator.id], message.id)

    evaluator_run_mock.assert_not_called()
    assert EvaluationResult.objects.filter(run=run, message=message, evaluator=evaluator).count() == 1


@pytest.mark.django_db()
@patch("apps.evaluations.models.Evaluator.run")
def test_evaluate_single_message_only_runs_missing_evaluator(evaluator_run_mock, coordination_run):
    run, evaluator1, message = coordination_run
    evaluator2 = EvaluatorFactory.create(team=run.team)
    # evaluator1 already done; evaluator2 outstanding
    EvaluationResultFactory.create(
        team=run.team, run=run, evaluator=evaluator1, message=message, output={"result": {"score": 1}}
    )
    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"result": {"score": 2}}))

    evaluate_single_message_task(run.id, [evaluator1.id, evaluator2.id], message.id)

    assert evaluator_run_mock.call_count == 1
    assert EvaluationResult.objects.filter(run=run, message=message, evaluator=evaluator2).exists()
    assert EvaluationResult.objects.filter(run=run, message=message).count() == 2


@pytest.mark.django_db()
@patch("apps.evaluations.models.Evaluator.run")
def test_evaluate_single_message_duplicate_insert_is_swallowed(evaluator_run_mock, coordination_run):
    run, evaluator, message = coordination_run
    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"result": {"score": 3}}))

    # Pre-create the row so the task's create() collides with the unique constraint
    # AFTER its skip check (simulated by patching the skip check to see nothing).
    EvaluationResultFactory.create(
        team=run.team, run=run, evaluator=evaluator, message=message, output={"result": {"pre": True}}
    )
    with patch("apps.evaluations.tasks._pending_evaluator_ids", return_value=[evaluator.id]):
        # Should not raise despite the pre-existing row.
        evaluate_single_message_task(run.id, [evaluator.id], message.id)

    assert EvaluationResult.objects.filter(run=run, message=message, evaluator=evaluator).count() == 1
