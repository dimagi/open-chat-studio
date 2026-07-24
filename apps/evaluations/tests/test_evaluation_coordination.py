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
from apps.evaluations.models import EvaluationResult, EvaluationRun, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tasks import (
    _mark_run_failed,
    _publish_tick,
    _TickResult,
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
def test_sweep_pending_dispatches_first_batch(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert set(run.in_flight) == {m.id for m in messages}
    assert run.batch_dispatched_at is not None
    # 5 messages, BATCH_SIZE=3 => 2 batches
    assert delay_mock.call_count == 2


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_dispatch_size_capped(delay_mock, _publish):
    # 40 messages, dispatch caps at BATCHES_PER_TICK*BATCH_SIZE = 30 => 10 batches
    run, evaluators, messages = _make_run(message_count=40, status=EvaluationRunStatus.PENDING)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert len(run.in_flight) == 30
    assert delay_mock.call_count == 10


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_dispatches_next_batch_when_current_done(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=40, status=EvaluationRunStatus.PROCESSING)
    batch1 = messages[:30]
    run.in_flight = [m.id for m in batch1]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, batch1)

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
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
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
def test_sweep_fresh_batch_in_progress_is_noop(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    # no results yet, batch just dispatched => not done, not stalled

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
    run.batch_dispatched_at = timezone.now() - timedelta(hours=1)  # older than STALL_TIMEOUT
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
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
def test_sweep_old_batch_with_fresh_results_is_not_stalled(delay_mock, _publish):
    """The newest-result arm of the staleness floor: an old batch_dispatched_at alone
    must not trigger a re-dispatch while fresh results are still landing."""
    run, evaluators, messages = _make_run(evaluator_count=2, message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now() - timedelta(hours=1)  # well past STALL_TIMEOUT
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    # Results from only one of the two evaluators: the batch is not done, but the rows
    # carry created_at = now (auto_now_add), so the newest-result signal is fresh.
    _complete_messages(run, [evaluators[0]], messages)

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert set(run.in_flight) == {m.id for m in messages}  # unchanged, no re-dispatch
    assert run.stall_count == 0
    delay_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_counts_partially_evaluated_message_as_remaining(delay_mock, _publish):
    """A message with a result from only one of two evaluators is still remaining;
    if a single result were enough the tick below would complete the run."""
    run, evaluators, messages = _make_run(evaluator_count=2, message_count=2, status=EvaluationRunStatus.PROCESSING)
    fully_done, partial = messages
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now()
    run.save(update_fields=["in_flight", "batch_dispatched_at"])
    _complete_messages(run, evaluators, [fully_done])  # both evaluators done
    _complete_messages(run, [evaluators[0]], [partial])  # only one of the two

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING  # not COMPLETED
    delay_mock.assert_not_called()  # fresh batch still in progress => no-op tick


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_fails_after_max_stalls_without_progress(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=3, status=EvaluationRunStatus.PROCESSING)
    run.in_flight = [m.id for m in messages]
    run.batch_dispatched_at = timezone.now() - timedelta(hours=1)
    run.stall_count = 2  # already stalled twice with no progress
    run.save(update_fields=["in_flight", "batch_dispatched_at", "stall_count"])
    # no results at all => no progress

    coordinate_evaluation_runs()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.FAILED
    assert run.error_message
    delay_mock.assert_not_called()


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_run_evaluation_task_fast_path_dispatches_batch_one(delay_mock, _publish):
    run, evaluators, messages = _make_run(message_count=5, status=EvaluationRunStatus.PENDING)

    run_evaluation_task(run.id)

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.PROCESSING
    assert delay_mock.call_count == 2  # 5 messages => 2 batches


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.models.Evaluator.run")
def test_full_run_reaches_completion_over_multiple_ticks(evaluator_run_mock, _publish):
    """A run larger than one batch completes across several ticks, with no duplicate results.

    Each tick dispatches batches into `dispatched`; we drain them by calling the real
    evaluate_message_batch (which runs evaluate_single_message_task in-process), then
    tick again, until the run completes.
    """
    evaluator_run_mock.return_value = Mock(model_dump=Mock(return_value={"result": {"score": 1}}))
    run, evaluators, messages = _make_run(evaluator_count=1, message_count=35, status=EvaluationRunStatus.PENDING)

    dispatched: list[list[int]] = []

    def capture(run_id, batch):
        dispatched.append(batch)

    for _ in range(10):  # safety bound
        run.refresh_from_db()
        if run.status == EvaluationRunStatus.COMPLETED:
            break
        with patch("apps.evaluations.tasks.evaluate_message_batch.delay", side_effect=capture):
            coordinate_evaluation_runs()
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
def test_mark_run_failed_sets_status_and_publishes_stop_state():
    run = EvaluationRunFactory.create(
        job_id="job-123", taskbadger_task_id="tb-1", status=EvaluationRunStatus.PROCESSING
    )

    with (
        patch("apps.evaluations.tasks.current_app") as app_mock,
        patch("apps.evaluations.tasks.taskbadger.update_task_safe") as taskbadger_mock,
    ):
        _mark_run_failed(run.id, "boom")

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.FAILED
    assert run.error_message == "boom"
    # The stop publish uses "SUCCESS" so the poller reloads; the page then shows FAILED.
    app_mock.backend.store_result.assert_called_once_with(
        "job-123",
        {"pending": False, "current": 0, "total": 0, "percent": 100.0, "description": "0 of 0 evaluated"},
        "SUCCESS",
    )
    taskbadger_mock.assert_called_once_with("tb-1", value=0, value_max=0, status=StatusEnum.ERROR)


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
