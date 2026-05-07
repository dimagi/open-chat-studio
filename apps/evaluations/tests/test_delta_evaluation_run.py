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
