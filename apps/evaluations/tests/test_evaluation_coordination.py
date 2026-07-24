from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
import time_machine
from django.db import IntegrityError
from django.utils import timezone

from apps.evaluations.const import PREVIEW_SAMPLE_SIZE
from apps.evaluations.models import EvaluationResult, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tasks import (
    coordinate_evaluation_runs,
    evaluate_message_batch,
    evaluate_single_message_task,
    run_evaluation_task,
)
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


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.evaluate_single_message_task")
def test_evaluate_message_batch_runs_each_message(single_mock, coordination_run):
    run, evaluator, message = coordination_run
    run.status = EvaluationRunStatus.PROCESSING
    run.save(update_fields=["status"])
    message2 = EvaluationMessageFactory.create()

    evaluate_message_batch(run.id, [message.id, message2.id])

    assert single_mock.call_count == 2
    single_mock.assert_any_call(run.id, run.evaluator_ids, message.id)
    single_mock.assert_any_call(run.id, run.evaluator_ids, message2.id)


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.evaluate_single_message_task")
def test_evaluate_message_batch_skips_when_run_not_processing(single_mock, coordination_run):
    run, evaluator, message = coordination_run  # status defaults to PENDING
    evaluate_message_batch(run.id, [message.id])
    single_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.evaluate_single_message_task")
def test_evaluate_message_batch_skips_deleted_run(single_mock, coordination_run):
    run, evaluator, message = coordination_run
    run_id = run.id
    run.delete()
    evaluate_message_batch(run_id, [message.id])
    single_mock.assert_not_called()


def _make_run(evaluator_count=1, message_count=5, status=EvaluationRunStatus.PENDING):
    """Build a run with a frozen plan of `message_count` messages and `evaluator_count` evaluators."""
    team = TeamWithUsersFactory.create()
    config = EvaluationConfigFactory.create(team=team)
    evaluators = [EvaluatorFactory.create(team=team) for _ in range(evaluator_count)]
    config.evaluators.set(evaluators)
    messages = [EvaluationMessageFactory.create() for _ in range(message_count)]
    config.dataset.messages.add(*messages)
    run = EvaluationRunFactory.create(config=config, team=team, status=status, evaluator_ids=[e.id for e in evaluators])
    run.scoped_messages.add(*messages)
    return run, evaluators, messages


def _complete_messages(run, evaluators, messages):
    for message in messages:
        for evaluator in evaluators:
            EvaluationResultFactory.create(
                team=run.team, run=run, evaluator=evaluator, message=message, output={"result": {"ok": 1}}
            )


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_pending_dispatches_first_wave(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert set(run.in_flight) == {m.id for m in messages}
    assert run.wave_dispatched_at is not None
    # 5 messages, BATCH_SIZE=3 => 2 batches
    assert delay_mock.call_count == 2


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_wave_size_capped(delay_mock, _publish):
    # 40 messages, wave caps at WAVE_SIZE*BATCH_SIZE = 30 => 10 batches
    run, evaluators, messages = _make_run(message_count=40, status=EvaluationRunStatus.PENDING)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert len(run.in_flight) == 30
    assert delay_mock.call_count == 10


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_dispatches_next_wave_when_current_done(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=40, status=EvaluationRunStatus.PROCESSING)
    wave1 = messages[:30]
    run.in_flight = [m.id for m in wave1]
    run.wave_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "wave_dispatched_at"])
    _complete_messages(run, evaluators, wave1)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert set(run.in_flight) == {m.id for m in messages[30:]}  # remaining 10
    assert delay_mock.call_count == 4  # 10 messages => ceil(10/3)=4 batches


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_completes_when_nothing_remains(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.wave_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "wave_dispatched_at"])
    _complete_messages(run, evaluators, messages)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    assert run.finished_at is not None
    delay_mock.assert_not_called()
    assert run.aggregates.exists()  # compute_aggregates_for_run ran


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_empty_plan_completes_immediately(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=0, status=EvaluationRunStatus.PENDING)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    delay_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_fresh_wave_in_progress_is_noop(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.wave_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "wave_dispatched_at"])
    # no results yet, wave just dispatched => not done, not stalled

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    delay_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_stalled_redispatches_unfinished(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.wave_dispatched_at = timezone.now() - timedelta(hours=1)  # older than STALL_TIMEOUT
    run.save(update_fields=["in_flight", "wave_dispatched_at"])
    # Land the completed results an hour ago so the newest-result staleness signal is old too
    # (created_at is auto_now_add, so we must travel to backdate it).
    with time_machine.travel(timezone.now() - timedelta(hours=1)):
        _complete_messages(run, evaluators, messages[:2])  # 2 done, 3 unfinished

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert set(run.in_flight) == {m.id for m in messages[2:]}
    assert delay_mock.call_count == 1  # 3 unfinished => 1 batch
    assert run.stall_count == 1  # progress was made (2 completed) => reset to 1


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_fails_after_max_stalls_without_progress(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.wave_dispatched_at = timezone.now() - timedelta(hours=1)
    run.stall_count = 2  # already stalled twice with no progress
    run.save(update_fields=["in_flight", "wave_dispatched_at", "stall_count"])
    # no results at all => no progress

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.FAILED
    assert run.error_message
    delay_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_run_evaluation_task_fast_path_dispatches_wave_one(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)

    run_evaluation_task(run.id)

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert delay_mock.call_count == 2  # 5 messages => 2 batches
