import pytest

from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
def test_create_experiment_version():
    original_experiment = ExperimentFactory()

    assert original_experiment.version_number == 1

    new_version = original_experiment.create_new_version()
    assert new_version != original_experiment
    assert original_experiment.version_number == 2
    assert original_experiment.working_experiment is None
    assert new_version.version_number == 1
    assert new_version.working_experiment == original_experiment
