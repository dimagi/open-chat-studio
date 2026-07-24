from unittest.mock import patch

import pytest

from apps.evaluations.models import EvaluationResult, EvaluationRun, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tasks import run_evaluation_task
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
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
def test_full_run_freezes_all_dataset_messages():
    config = EvaluationConfigFactory.create()
    dataset_ids = set(config.dataset.messages.values_list("id", flat=True))
    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        run = config.run()
    assert run.type == EvaluationRunType.FULL
    assert set(run.scoped_messages.values_list("id", flat=True)) == dataset_ids
    mock_delay.assert_called_once_with(run.id)


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_run_evaluation_task_dispatches_only_scoped_messages_for_delta(delay_mock, _publish):
    config = EvaluationConfigFactory.create()
    evaluator = EvaluatorFactory.create(team=config.team)
    config.evaluators.set([evaluator])
    in_scope = EvaluationMessageFactory.create()
    out_of_scope = EvaluationMessageFactory.create()
    config.dataset.messages.add(in_scope, out_of_scope)

    run = EvaluationRun.objects.create(
        team=config.team,
        config=config,
        status=EvaluationRunStatus.PENDING,
        type=EvaluationRunType.DELTA,
        evaluator_ids=[evaluator.id],
    )
    run.scoped_messages.add(in_scope)

    run_evaluation_task(run.id)

    dispatched = [message_id for call in delay_mock.call_args_list for message_id in call.args[1]]
    assert dispatched == [in_scope.id]


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
