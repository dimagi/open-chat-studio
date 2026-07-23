from unittest.mock import patch

import pytest
from django.db import IntegrityError

from apps.evaluations.const import PREVIEW_SAMPLE_SIZE
from apps.evaluations.models import EvaluationRunType
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)


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
