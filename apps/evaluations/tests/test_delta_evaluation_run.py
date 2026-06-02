from unittest.mock import patch

import pytest

from apps.evaluations.models import EvaluationResult, EvaluationRun, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tasks import run_evaluation_task
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)


@pytest.mark.django_db()
def test_run_with_scoped_messages_persists_scope():
    config = EvaluationConfigFactory.create()
    msg1 = EvaluationMessageFactory.create()
    msg2 = EvaluationMessageFactory.create()

    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        run = config.run(run_type=EvaluationRunType.DELTA, scoped_messages=[msg1, msg2])

    assert run.type == EvaluationRunType.DELTA
    assert set(run.scoped_messages.all()) == {msg1, msg2}
    mock_delay.assert_called_once_with(run.id)


@pytest.mark.django_db()
def test_delta_run_rejects_messages_already_evaluated_by_prior_run():
    """The DELTA invariant: scoped_messages must be disjoint from prior runs of the config.

    Auto-population only ever feeds brand-new sessions, so the sole live caller never
    trips this — but a future caller passing an already-evaluated message would silently
    break the run-set undo, so we fail loudly instead.
    """
    config = EvaluationConfigFactory.create()
    evaluator = EvaluatorFactory.create(team=config.team)
    config.evaluators.add(evaluator)
    message = EvaluationMessageFactory.create()
    config.dataset.messages.add(message)

    # A prior run already evaluated `message`.
    prior_run = EvaluationRunFactory.create(
        team=config.team, config=config, status=EvaluationRunStatus.COMPLETED, type=EvaluationRunType.FULL
    )
    EvaluationResultFactory.create(team=config.team, evaluator=evaluator, message=message, run=prior_run)

    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        with pytest.raises(ValueError, match="overlap"):
            config.run(run_type=EvaluationRunType.DELTA, scoped_messages=[message])

    mock_delay.assert_not_called()
    # The overlap check must reject before creating a run row.
    assert not EvaluationRun.objects.filter(config=config, type=EvaluationRunType.DELTA).exists()


@pytest.mark.django_db()
def test_delta_run_allows_messages_not_seen_by_prior_runs():
    """A DELTA over genuinely new messages is accepted."""
    config = EvaluationConfigFactory.create()
    evaluator = EvaluatorFactory.create(team=config.team)
    config.evaluators.add(evaluator)
    seen = EvaluationMessageFactory.create()
    fresh = EvaluationMessageFactory.create()
    config.dataset.messages.add(seen, fresh)

    prior_run = EvaluationRunFactory.create(
        team=config.team, config=config, status=EvaluationRunStatus.COMPLETED, type=EvaluationRunType.FULL
    )
    EvaluationResultFactory.create(team=config.team, evaluator=evaluator, message=seen, run=prior_run)

    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        run = config.run(run_type=EvaluationRunType.DELTA, scoped_messages=[fresh])

    assert set(run.scoped_messages.all()) == {fresh}
    mock_delay.assert_called_once_with(run.id)


@pytest.mark.django_db()
def test_run_without_scoped_messages_has_empty_scope():
    config = EvaluationConfigFactory.create()
    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        run = config.run()
    assert run.type == EvaluationRunType.FULL
    assert run.scoped_messages.count() == 0
    mock_delay.assert_called_once_with(run.id)


@pytest.mark.django_db()
def test_run_evaluation_task_evaluates_only_scoped_messages_for_delta(monkeypatch):
    """Delta runs only fan out per scoped message, ignoring other dataset rows."""
    config = EvaluationConfigFactory.create()
    in_scope = EvaluationMessageFactory.create()
    out_of_scope = EvaluationMessageFactory.create()
    config.dataset.messages.add(in_scope, out_of_scope)

    run = EvaluationRun.objects.create(
        team=config.team,
        config=config,
        status=EvaluationRunStatus.PENDING,
        type=EvaluationRunType.DELTA,
    )
    run.scoped_messages.add(in_scope)

    dispatched_message_ids: list[int] = []

    def fake_chunks(chunked_args, _chunk_size):
        for _evaluation_run_id, _evaluator_ids, message_id in chunked_args:
            dispatched_message_ids.append(message_id)

        class _Group:
            def group(self):
                return self

        return _Group()

    class _ChordResult:
        parent = type("Parent", (), {"id": "fake", "save": lambda self: None})()

    def fake_chord(_g):
        def _runner(_callback):
            return _ChordResult()

        return _runner

    monkeypatch.setattr("apps.evaluations.tasks.evaluate_single_message_task.chunks", fake_chunks)
    monkeypatch.setattr("apps.evaluations.tasks.chord", fake_chord)

    run_evaluation_task(run.id)

    assert dispatched_message_ids == [in_scope.id]


@pytest.mark.django_db()
def test_get_table_data_delta_only_returns_scoped_messages():
    config = EvaluationConfigFactory.create()
    evaluator = EvaluatorFactory.create(team=config.team)
    config.evaluators.add(evaluator)

    in_scope = EvaluationMessageFactory.create()
    out_of_scope = EvaluationMessageFactory.create()
    config.dataset.messages.add(in_scope, out_of_scope)

    run = EvaluationRun.objects.create(team=config.team, config=config, type=EvaluationRunType.DELTA)
    run.scoped_messages.add(in_scope)

    EvaluationResult.objects.create(
        team=config.team,
        run=run,
        evaluator=evaluator,
        message=in_scope,
        output={"message": {"input": {"content": "hi"}, "output": {"content": "hello"}}},
    )
    EvaluationResult.objects.create(
        team=config.team,
        run=run,
        evaluator=evaluator,
        message=out_of_scope,
        output={"message": {"input": {"content": "n/a"}, "output": {"content": "n/a"}}},
    )

    rows = run.get_table_data()
    message_ids = {row["message_id"] for row in rows}
    assert message_ids == {in_scope.id}
