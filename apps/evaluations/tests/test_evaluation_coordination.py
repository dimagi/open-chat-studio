from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
import time_machine
from celery_progress.backend import PROGRESS_STATE
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from taskbadger import StatusEnum

from apps.evaluations.const import PREVIEW_SAMPLE_SIZE
from apps.evaluations.models import (
    NON_TERMINAL_RUN_STATUSES,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
)
from apps.evaluations.tasks import (
    _ensure_taskbadger_task,
    _publish_tick,
    _TickResult,
    coordinate_evaluation_runs,
    drive_evaluation_run,
    evaluate_message,
    evaluate_message_batch,
    finalize_evaluation_run,
)
from apps.evaluations.tests.coordination import sweep
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory
from apps.utils.factories.user import GroupFactory


@pytest.mark.django_db()
def test_coordination_fields_default_empty():
    run = EvaluationRunFactory.create()
    assert run.in_flight == []
    assert run.evaluator_ids == []
    assert run.batch_dispatched_at is None
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

    run = config.run(run_type=EvaluationRunType.FULL)

    assert set(run.scoped_messages.values_list("id", flat=True)) == all_ids
    assert run.evaluator_ids == evaluator_ids
    assert run.job_id  # a uuid was assigned
    assert run.status == EvaluationRunStatus.PENDING  # left for the coordinator to start


@pytest.mark.django_db()
def test_run_freezes_preview_sample():
    config = EvaluationConfigFactory.create()
    for _ in range(PREVIEW_SAMPLE_SIZE + 5):
        config.dataset.messages.add(EvaluationMessageFactory.create())

    run = config.run(run_type=EvaluationRunType.PREVIEW)

    assert run.scoped_messages.count() == PREVIEW_SAMPLE_SIZE


@pytest.mark.django_db()
def test_run_freezes_delta_explicit_list():
    config = EvaluationConfigFactory.create()
    msg1 = EvaluationMessageFactory.create()
    msg2 = EvaluationMessageFactory.create()

    run = config.run(run_type=EvaluationRunType.DELTA, scoped_message_ids=[msg1.id, msg2.id])

    assert set(run.scoped_messages.all()) == {msg1, msg2}


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_run_dispatches_nothing_and_waits_for_the_coordinator(dispatch_mock):
    """Creating a run must not dispatch work; only coordinate_evaluation_runs does that."""
    config = EvaluationConfigFactory.create()

    run = config.run()

    dispatch_mock.assert_not_called()
    assert run.status == EvaluationRunStatus.PENDING
    assert run.in_flight == []
    assert run.batch_dispatched_at is None


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

    evaluate_message(run.id, [evaluator.id], message.id)

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

    evaluate_message(run.id, [evaluator1.id, evaluator2.id], message.id)

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
        evaluate_message(run.id, [evaluator.id], message.id)

    assert EvaluationResult.objects.filter(run=run, message=message, evaluator=evaluator).count() == 1


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.evaluate_message")
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
@patch("apps.evaluations.tasks.evaluate_message")
def test_evaluate_message_batch_skips_when_run_not_processing(single_mock, coordination_run):
    run, evaluator, message = coordination_run  # status defaults to PENDING
    evaluate_message_batch(run.id, [message.id])
    single_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.evaluate_message")
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
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_pending_run_with_an_unconfigured_evaluator_fails_before_dispatching(dispatch_mock, _publish):
    """One error, not one per message: the pre-flight fails the run instead of dispatching."""
    run, evaluators, _messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)
    evaluators[0].llm_provider = None
    evaluators[0].save(update_fields=["llm_provider"])

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.FAILED
    assert evaluators[0].name in run.error_message
    assert "select a provider and model" in run.error_message
    assert run.finished_at is not None  # or the run renders no finish time and no duration
    assert dispatch_mock.call_count == 0
    assert EvaluationResult.objects.filter(run=run).count() == 0


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_pending_run_with_a_python_evaluator_needs_no_provider(dispatch_mock, _publish):
    """PythonEvaluator has no LLM dependency, so the pre-flight must not fail it."""
    team = TeamWithUsersFactory.create()
    config = EvaluationConfigFactory.create(team=team)
    evaluator = EvaluatorFactory.create(team=team, type="PythonEvaluator", params={"code": "def main(**kwargs): pass"})
    config.evaluators.set([evaluator])
    message = EvaluationMessageFactory.create()
    config.dataset.messages.add(message)
    run = EvaluationRunFactory.create(
        config=config, team=team, status=EvaluationRunStatus.PENDING, evaluator_ids=[evaluator.id]
    )
    run.scoped_messages.add(message)

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert dispatch_mock.call_count == 1


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_pending_dispatches_first_batch(dispatch_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert set(run.in_flight) == {m.id for m in messages}
    assert run.batch_dispatched_at is not None
    # 5 messages, BATCH_SIZE=3 => 2 batches
    assert dispatch_mock.call_count == 2


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_dispatch_size_capped(dispatch_mock, _publish):
    # 40 messages, dispatch caps at BATCHES_PER_TICK*BATCH_SIZE = 30 => 10 batches
    run, evaluators, messages = _make_run(message_count=40, status=EvaluationRunStatus.PENDING)

    sweep()

    run.refresh_from_db()
    assert len(run.in_flight) == 30
    assert dispatch_mock.call_count == 10


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_dispatches_next_batch_when_current_done(dispatch_mock, _publish):
    run, evaluators, messages = _make_run(message_count=40, status=EvaluationRunStatus.PROCESSING)
    batch1 = messages[:30]
    run.in_flight = [m.id for m in batch1]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, batch1)

    sweep()

    run.refresh_from_db()
    assert set(run.in_flight) == {m.id for m in messages[30:]}  # remaining 10
    assert dispatch_mock.call_count == 4  # 10 messages => ceil(10/3)=4 batches


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_completes_when_nothing_remains(dispatch_mock, _publish):
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, messages)

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    assert run.finished_at is not None
    dispatch_mock.assert_not_called()
    assert run.aggregates.exists()  # compute_aggregates_for_run ran


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_completion_survives_a_failing_finalization(dispatch_mock, _publish):
    """A crash in the completion side effects must not rewind the run to PROCESSING.

    When the transition shared the tick's transaction, a side effect that died took the
    COMPLETED status down with it, so the next tick saw the same finished run and re-ran
    the same failing work every beat interval — the OOM restart loop this guards against.
    """
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, messages)

    run_ids = list(EvaluationRun.objects.filter(status__in=NON_TERMINAL_RUN_STATUSES).values_list("id", flat=True))
    with patch("apps.evaluations.tasks.compute_aggregates_for_run", side_effect=MemoryError):
        for run_id in run_ids:
            drive_evaluation_run(run_id)
        with pytest.raises(MemoryError):
            finalize_evaluation_run(run.id)

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED  # terminal, so no later tick picks it up
    assert not run.aggregates.exists()  # the side effect really did fail
    assert run.finalized_at is None  # ...and the run does not claim otherwise


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.drive_evaluation_run.delay")
def test_beat_fans_out_one_tick_per_active_run(delay_mock):
    """One task per run, so a tick killed mid-flight cannot starve the runs behind it."""
    active = [_make_run(message_count=1, status=EvaluationRunStatus.PENDING)[0] for _ in range(3)]
    done, _evaluators, _messages = _make_run(message_count=1, status=EvaluationRunStatus.PROCESSING)
    done.mark_complete()

    coordinate_evaluation_runs()

    assert {call.args[0] for call in delay_mock.call_args_list} == {run.id for run in active}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("message_count", "clear_evaluators"),
    [
        pytest.param(0, False, id="empty-plan"),
        pytest.param(3, True, id="no-evaluators"),
    ],
)
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
@patch("apps.evaluations.tasks.finalize_evaluation_run.apply_async")
def test_completion_with_nothing_evaluated_skips_finalization(
    finalize_mock, _dispatch, _publish, message_count, clear_evaluators
):
    """A run that completes without evaluating anything has nothing to finalize.

    Beyond saving the pointless sweep: `reverse_stale_tags` reads the live config rather
    than the run's frozen evaluator snapshot, so finalizing the no-evaluators branch would
    walk the whole dataset with an empty applied-tag map and treat every managed tag as
    stale.
    """
    run, _evaluators, _messages = _make_run(message_count=message_count, status=EvaluationRunStatus.PENDING)
    if clear_evaluators:
        run.evaluator_ids = []
        run.save(update_fields=["evaluator_ids"])

    drive_evaluation_run(run.id)

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    finalize_mock.assert_not_called()
    # Nothing will finalize it, so it must not read as "aggregates still coming" either.
    assert run.finalized_at is not None
    assert not run.is_finalizing


@pytest.mark.django_db()
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.finalize_evaluation_run.apply_async", side_effect=RuntimeError("broker down"))
def test_failed_finalize_dispatch_still_publishes_completion(finalize_mock, publish_mock, _delay):
    """The run is terminal by the time finalization is dispatched, so no later tick would
    retry the publish. A broker error dispatching it must not strand the UI poller."""
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, messages)

    drive_evaluation_run(run.id)  # the dispatch error is logged by its broad handler

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    finalize_mock.assert_called_once()
    assert finalize_mock.call_args.kwargs["args"] == [run.id]
    publish_mock.assert_called_once()
    assert publish_mock.call_args.args[1].terminal == "success"


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
@patch("apps.evaluations.tasks.finalize_evaluation_run.apply_async")
def test_run_stays_unfinalized_until_finalization_runs(finalize_mock, _dispatch, _publish):
    """The completing tick must leave `finalized_at` unset.

    That tick also publishes the UI stop signal, so the results page reloads before
    `finalize_evaluation_run` has necessarily run. `finalized_at` is what lets the reloaded
    page tell "aggregates still coming" apart from "this run produced none", instead of
    rendering a completed run with a silently empty Aggregates section.
    """
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, messages)

    drive_evaluation_run(run.id)

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    finalize_mock.assert_called_once()
    assert finalize_mock.call_args.kwargs["args"] == [run.id]
    assert run.finalized_at is None
    assert run.is_finalizing

    finalize_evaluation_run(run.id)

    run.refresh_from_db()
    assert run.aggregates.exists()
    assert run.finalized_at is not None
    assert not run.is_finalizing


@pytest.mark.django_db()
def test_finalization_is_a_noop_for_a_deleted_run():
    finalize_evaluation_run(-1)  # must not raise


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [EvaluationRunStatus.PENDING, EvaluationRunStatus.PROCESSING, EvaluationRunStatus.FAILED],
)
def test_finalization_is_a_noop_for_a_run_that_is_not_completed(status):
    """Only a COMPLETED run has a full result set to aggregate, and the results UI only
    ever shows a COMPLETED run's aggregates — a stray dispatch must not write partial ones.
    """
    run, evaluators, messages = _make_run(message_count=2, status=status)
    _complete_messages(run, evaluators, messages)

    finalize_evaluation_run(run.id)

    assert not run.aggregates.exists()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_empty_plan_completes_immediately(dispatch_mock, _publish):
    run, evaluators, messages = _make_run(message_count=0, status=EvaluationRunStatus.PENDING)

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    dispatch_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_fresh_batch_in_progress_is_noop(dispatch_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    # no results yet, batch just dispatched => not done, not stalled

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    dispatch_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_stalled_redispatches_unfinished(dispatch_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now() - timedelta(hours=1)  # older than STALL_TIMEOUT
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    # Land the completed results an hour ago so the newest-result staleness signal is old too
    # (created_at is auto_now_add, so we must travel to backdate it).
    with time_machine.travel(timezone.now() - timedelta(hours=1)):
        _complete_messages(run, evaluators, messages[:2])  # 2 done, 3 unfinished

    sweep()

    run.refresh_from_db()
    assert set(run.in_flight) == {m.id for m in messages[2:]}
    assert dispatch_mock.call_count == 1  # 3 unfinished => 1 batch
    assert run.stall_count == 1  # progress was made (2 completed) => reset to 1


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_old_batch_with_fresh_results_is_not_stalled(dispatch_mock, _publish):
    """The newest-result arm of the staleness floor: an old batch_dispatched_at alone
    must not trigger a re-dispatch while fresh results are still landing."""
    run, evaluators, messages = _make_run(evaluator_count=2, message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now() - timedelta(hours=1)  # well past STALL_TIMEOUT
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    # Results from only one of the two evaluators: the batch is not done, but the rows
    # carry created_at = now (auto_now_add), so the newest-result signal is fresh.
    _complete_messages(run, [evaluators[0]], messages)

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert set(run.in_flight) == {m.id for m in messages}  # unchanged, no re-dispatch
    assert run.stall_count == 0
    dispatch_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_counts_partially_evaluated_message_as_remaining(dispatch_mock, _publish):
    """A message with a result from only one of two evaluators is still remaining;
    if a single result were enough the tick below would complete the run."""
    run, evaluators, messages = _make_run(evaluator_count=2, message_count=2, status=EvaluationRunStatus.PROCESSING)
    fully_done, partial = messages
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, [fully_done])  # both evaluators done
    _complete_messages(run, [evaluators[0]], [partial])  # only one of the two

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING  # not COMPLETED
    dispatch_mock.assert_not_called()  # fresh batch still in progress => no-op tick


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_sweep_fails_after_max_stalls_without_progress(dispatch_mock, _publish):
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now() - timedelta(hours=1)
    run.stall_count = 2  # already stalled twice with no progress
    run.save(update_fields=["in_flight", "batch_dispatched_at", "stall_count"])
    # no results at all => no progress

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.FAILED
    assert run.error_message
    assert run.finished_at is not None
    assert run.stall_count == 3  # mark_failed must not clobber the counter it is saved alongside
    dispatch_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.models.Evaluator.run")
def test_full_run_reaches_completion_over_multiple_ticks(evaluator_run_mock, _publish):
    """A run larger than one batch completes across several ticks, with no duplicate results.

    Each tick dispatches batches into `dispatched`; we drain them by calling the real
    evaluate_message_batch (which runs evaluate_message in-process), then
    tick again, until the run completes.
    """
    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"result": {"score": 1}}))
    run, evaluators, messages = _make_run(evaluator_count=1, message_count=35, status=EvaluationRunStatus.PENDING)

    dispatched: list[list[int]] = []

    def capture(args=None, **kwargs):
        _run_id, batch = args
        dispatched.append(batch)

    for _ in range(10):  # safety bound
        run.refresh_from_db()
        if run.status == EvaluationRunStatus.COMPLETED:
            break
        with patch("apps.evaluations.tasks.evaluate_message_batch.apply_async", side_effect=capture):
            sweep()
        # a "worker" drains everything dispatched this tick
        pending, dispatched = dispatched, []
        for batch in pending:
            evaluate_message_batch(run.id, batch)

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    # every message evaluated exactly once by the single evaluator
    assert EvaluationResult.objects.filter(run=run).count() == 35
    # no duplicates
    seen = set()
    for message_id, evaluator_id in EvaluationResult.objects.filter(run=run).values_list("message_id", "evaluator_id"):
        assert (message_id, evaluator_id) not in seen
        seen.add((message_id, evaluator_id))


@pytest.mark.django_db()
def test_ensure_taskbadger_task_records_run_context():
    config = EvaluationConfigFactory.create()
    run = EvaluationRunFactory.create(config=config, team=config.team, type=EvaluationRunType.DELTA)

    with patch("apps.evaluations.tasks.taskbadger.create_task_safe") as create_mock:
        create_mock.return_value = Mock(id="tb-new")
        _ensure_taskbadger_task(run, total=5)

    assert create_mock.call_args.kwargs["name"] == "Evaluation run"
    assert create_mock.call_args.kwargs["data"] == {
        "run_id": run.id,
        "run_type": "delta",
        "config_id": config.id,
        "config_name": config.name,
        "dataset_id": config.dataset_id,
        "dataset_name": config.dataset.name,
    }
    run.refresh_from_db()
    assert run.taskbadger_task_id == "tb-new"


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_a_tick_overlapping_the_create_does_not_make_a_second_taskbadger_task(_batch, _publish):
    """Creating twice would mean two root tasks, not a no-op: the create is not idempotent.

    Interleaves the two ticks at the only point where the empty-id guard cannot help — the
    first tick has committed, so its row lock is gone, but has not stored the new id yet.
    Only the PENDING claim keeps the second tick out of the create.
    """
    run, _evaluators, _messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)
    overlapped = False

    def drive_again_mid_create(**kwargs):
        nonlocal overlapped
        if not overlapped:  # once: the second tick must not recurse back into the create
            overlapped = True
            drive_evaluation_run(run.id)
        return Mock(id="tb-run")

    with patch("apps.evaluations.tasks.taskbadger.create_task_safe", side_effect=drive_again_mid_create) as create_mock:
        drive_evaluation_run(run.id)

    assert overlapped  # the interleaving happened, so the assertion below means something
    create_mock.assert_called_once()
    run.refresh_from_db()
    assert run.taskbadger_task_id == "tb-run"


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_a_run_past_its_first_tick_is_not_given_a_taskbadger_task(_batch, _publish):
    """Deliberate: retrying the create on a later tick is what let overlapping ticks double up.

    Reachable when the first tick's create failed, or for a run already in flight when this
    shipped. The run stays unmonitored rather than picking up a task mid-flight.
    """
    run, _evaluators, _messages = _make_run(message_count=5, status=EvaluationRunStatus.PROCESSING)
    assert run.taskbadger_task_id == ""

    with patch("apps.evaluations.tasks.taskbadger.create_task_safe") as create_mock:
        drive_evaluation_run(run.id)

    create_mock.assert_not_called()


@pytest.mark.django_db()
def test_ensure_taskbadger_task_is_free_once_created(django_assert_num_queries):
    """The config lookup must stay below the task-id guard: it runs once per run, not once per tick."""
    run = EvaluationRunFactory.create(taskbadger_task_id="tb-1")

    with (
        patch("apps.evaluations.tasks.taskbadger.create_task_safe") as create_mock,
        django_assert_num_queries(0),
    ):
        _ensure_taskbadger_task(run, total=5)

    create_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.finalize_evaluation_run.apply_async")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_dispatched_tasks_are_nested_under_the_runs_taskbadger_task(batch_mock, finalize_mock, _publish):
    """The batch and finalize tasks are dispatched as children of the run's Taskbadger task.

    The run's task is created by the same tick that dispatches the first batches, so it has
    to exist before they go out or that first tick's batches end up unparented.
    """
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)

    with patch("apps.evaluations.tasks.taskbadger.create_task_safe", return_value=Mock(id="tb-run")):
        drive_evaluation_run(run.id)

        assert batch_mock.call_count == 2  # 5 messages, BATCH_SIZE=3
        for call in batch_mock.call_args_list:
            assert call.kwargs["headers"] == {"taskbadger_kwargs": {"parent": "tb-run"}}

        _complete_messages(run, evaluators, messages)
        drive_evaluation_run(run.id)

    finalize_mock.assert_called_once_with(args=[run.id], headers={"taskbadger_kwargs": {"parent": "tb-run"}})


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.apply_async")
def test_dispatch_omits_parent_when_the_run_has_no_taskbadger_task(batch_mock, _publish):
    """An explicit `parent=None` would suppress the Celery integration's own nesting."""
    run, _evaluators, _messages = _make_run(message_count=1, status=EvaluationRunStatus.PENDING)

    with patch("apps.evaluations.tasks.taskbadger.create_task_safe", return_value=None):
        drive_evaluation_run(run.id)

    batch_mock.assert_called_once()
    assert "headers" not in batch_mock.call_args.kwargs


@pytest.mark.django_db()
def test_publish_tick_writes_progress_to_result_backend():
    run = EvaluationRunFactory.create(job_id="job-123")  # taskbadger_task_id is empty

    with (
        patch("apps.evaluations.tasks.current_app") as app_mock,
        patch("apps.evaluations.tasks.taskbadger.update_task_safe") as taskbadger_mock,
    ):
        _publish_tick(run, _TickResult(batches=[], done=3, total=5, terminal=None))

    app_mock.backend.store_result.assert_called_once_with(
        "job-123",
        {"pending": False, "current": 3, "total": 5, "percent": 60.0, "description": "3 of 5 evaluated"},
        PROGRESS_STATE,
    )
    taskbadger_mock.assert_not_called()  # no taskbadger task registered for the run


@pytest.mark.django_db()
def test_publish_tick_updates_taskbadger_when_task_id_set():
    run = EvaluationRunFactory.create(job_id="job-123", taskbadger_task_id="tb-1")

    with (
        patch("apps.evaluations.tasks.current_app"),
        patch("apps.evaluations.tasks.taskbadger.update_task_safe") as taskbadger_mock,
    ):
        _publish_tick(run, _TickResult(batches=[], done=3, total=5, terminal=None))

    taskbadger_mock.assert_called_once_with("tb-1", value=3, value_max=5)


@pytest.mark.django_db()
def test_publish_tick_terminal_success_publishes_stop_state():
    run = EvaluationRunFactory.create(job_id="job-123", taskbadger_task_id="tb-1")

    with (
        patch("apps.evaluations.tasks.current_app") as app_mock,
        patch("apps.evaluations.tasks.taskbadger.update_task_safe") as taskbadger_mock,
    ):
        _publish_tick(run, _TickResult(batches=[], done=5, total=5, terminal="success"))

    # "SUCCESS" makes the celery_progress poller stop and reload the page.
    app_mock.backend.store_result.assert_called_once_with(
        "job-123",
        {"pending": False, "current": 5, "total": 5, "percent": 100.0, "description": "5 of 5 evaluated"},
        "SUCCESS",
    )
    taskbadger_mock.assert_called_once_with("tb-1", value=5, value_max=5, status=StatusEnum.SUCCESS)


@pytest.mark.django_db()
def test_result_home_sets_celery_job_id_while_processing(client):
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(EvaluationRun),
        codename="view_evaluationrun",
    )
    view_group = GroupFactory.create(name="evaluations-view-only")
    view_group.permissions.add(view_perm)
    membership = MembershipFactory.create(groups=[view_group])
    team = membership.team
    user = membership.user
    config = EvaluationConfigFactory.create(team=team)
    run = EvaluationRunFactory.create(
        config=config, team=team, status=EvaluationRunStatus.PROCESSING, job_id="progress-key-123"
    )
    client.force_login(user)

    url = reverse("evaluations:evaluation_results_home", args=[team.slug, config.id, run.id])
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["celery_job_id"] == "progress-key-123"
    assert "group_job_id" not in response.context
    # No count yet, not a zero count — the template must render "—", not "0".
    assert response.context["total_results"] is None


@pytest.mark.django_db()
def test_result_home_total_results_is_zero_not_none_for_a_terminal_run_with_no_results(client):
    """A completed run with no EvaluationResult rows (empty dataset, or every evaluator
    failed before producing one) is a real zero, not "no count yet" - the template must
    render "0", not the pending/processing state's "—" placeholder."""
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(EvaluationRun),
        codename="view_evaluationrun",
    )
    view_group = GroupFactory.create(name="evaluations-view-only")
    view_group.permissions.add(view_perm)
    membership = MembershipFactory.create(groups=[view_group])
    team = membership.team
    user = membership.user
    config = EvaluationConfigFactory.create(team=team)
    run = EvaluationRunFactory.create(config=config, team=team, status=EvaluationRunStatus.COMPLETED)
    client.force_login(user)

    url = reverse("evaluations:evaluation_results_home", args=[team.slug, config.id, run.id])
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["total_results"] == 0
    content = response.content.decode()
    assert '<div class="text-3xl font-bold mt-2">0</div>' in content
