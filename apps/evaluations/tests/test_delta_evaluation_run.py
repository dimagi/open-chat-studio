from unittest.mock import patch

import pytest

from apps.evaluations.models import EvaluationRunType
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
)


@pytest.mark.django_db()
def test_evaluation_run_can_be_created_as_delta_with_scope():
    run = EvaluationRunFactory.create(type=EvaluationRunType.DELTA)
    msg = EvaluationMessageFactory.create()
    run.scoped_messages.add(msg)

    run.refresh_from_db()
    assert run.type == EvaluationRunType.DELTA
    assert list(run.scoped_messages.all()) == [msg]


@pytest.mark.django_db()
def test_full_run_has_empty_scope_by_default():
    run = EvaluationRunFactory.create(type=EvaluationRunType.FULL)
    assert run.scoped_messages.count() == 0


@pytest.mark.django_db()
def test_evaluation_config_auto_run_on_append_defaults_false():
    config = EvaluationConfigFactory.create()
    assert config.auto_run_on_append is False


@pytest.mark.django_db()
def test_evaluation_config_auto_run_on_append_can_be_set():
    config = EvaluationConfigFactory.create(auto_run_on_append=True)
    assert config.auto_run_on_append is True


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
def test_run_without_scoped_messages_has_empty_scope():
    config = EvaluationConfigFactory.create()
    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        run = config.run()
    assert run.type == EvaluationRunType.FULL
    assert run.scoped_messages.count() == 0
    mock_delay.assert_called_once_with(run.id)
