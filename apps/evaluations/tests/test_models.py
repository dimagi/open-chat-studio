import pytest

from apps.evaluations.models import ExperimentVersionSelection
from apps.utils.factories.evaluations import EvaluationConfigFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
def test_get_generation_experiment_version_specific():
    experiment = ExperimentFactory()
    config = EvaluationConfigFactory(
        experiment_version=experiment,
        base_experiment=experiment,
        version_selection_type=ExperimentVersionSelection.SPECIFIC,
    )
    assert config.get_generation_experiment_version() == experiment


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_working():
    working_experiment = ExperimentFactory()
    working_experiment.create_new_version("test")

    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=ExperimentVersionSelection.LATEST_WORKING,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version() == working_experiment


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_published():
    working_experiment = ExperimentFactory()
    working_experiment.create_new_version("test", make_default=True)
    published_version = working_experiment.create_new_version("test", make_default=True)

    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=ExperimentVersionSelection.LATEST_PUBLISHED,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version() == published_version


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_published_none():
    """When there are no published versions but we are targeting it, we use the working version"""

    working_experiment = ExperimentFactory()
    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=ExperimentVersionSelection.LATEST_PUBLISHED,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version().is_working_version


@pytest.mark.django_db()
def test_get_generation_experiment_version_no_base_experiment():
    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=None,
        version_selection_type=ExperimentVersionSelection.LATEST_WORKING,
    )
    assert config.get_generation_experiment_version() is None
