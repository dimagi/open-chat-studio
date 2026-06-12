from unittest.mock import patch

import pytest

from apps.experiments.models import Experiment
from apps.experiments.tasks import start_version_creation
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
class TestVersionOperationLock:
    def test_acquire_and_release(self):
        experiment = ExperimentFactory.create()
        assert not experiment.version_operation_in_progress

        assert experiment.acquire_version_operation_lock("task-1") is True
        assert experiment.version_operation_in_progress
        assert experiment.create_version_task_id == "task-1"

        Experiment.release_version_operation_lock(experiment.id)
        experiment.refresh_from_db()
        assert not experiment.version_operation_in_progress

    def test_second_acquire_is_rejected_while_in_flight(self):
        experiment = ExperimentFactory.create()
        assert experiment.acquire_version_operation_lock("task-1") is True

        contender = Experiment.objects.get(id=experiment.id)
        assert contender.acquire_version_operation_lock("task-2") is False

        experiment.refresh_from_db()
        assert experiment.create_version_task_id == "task-1"


@pytest.mark.django_db()
class TestStartVersionCreation:
    @patch("apps.experiments.tasks.async_create_experiment_version.apply_async")
    def test_acquires_lock_before_dispatch(self, apply_async):
        experiment = ExperimentFactory.create()

        assert start_version_creation(experiment, version_description="desc", make_default=True) is True

        experiment.refresh_from_db()
        assert experiment.version_operation_in_progress
        assert apply_async.call_count == 1
        kwargs = apply_async.call_args.kwargs
        assert kwargs["task_id"] == experiment.create_version_task_id
        assert kwargs["kwargs"] == {
            "experiment_id": experiment.id,
            "version_description": "desc",
            "make_default": True,
        }

    @patch("apps.experiments.tasks.async_create_experiment_version.apply_async")
    def test_rejected_while_another_operation_in_flight(self, apply_async):
        experiment = ExperimentFactory.create(create_version_task_id="other-operation")

        assert start_version_creation(experiment) is False

        apply_async.assert_not_called()
        experiment.refresh_from_db()
        assert experiment.create_version_task_id == "other-operation"

    @patch("apps.experiments.tasks.async_create_experiment_version.apply_async", side_effect=RuntimeError("boom"))
    def test_releases_lock_when_dispatch_fails(self, apply_async):
        experiment = ExperimentFactory.create()

        with pytest.raises(RuntimeError, match="boom"):
            start_version_creation(experiment)

        experiment.refresh_from_db()
        assert not experiment.version_operation_in_progress
