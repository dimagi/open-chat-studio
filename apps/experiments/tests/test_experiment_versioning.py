import pytest

from apps.experiments.models import ExperimentVersion
from apps.experiments.services import (
    populate_experiment_version_data,
    save_experiment_version,
    switch_default_experiment_version,
)
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def experiment():
    return ExperimentFactory(team=TeamWithUsersFactory())


class TestExperimentVersion:
    @pytest.mark.django_db()
    def test_save_experiment_version_success(self, experiment):
        result = save_experiment_version(experiment, is_default=True)
        assert result == "Success"
        versions = ExperimentVersion.objects.filter(experiment=experiment)
        assert versions.count() == 1
        assert versions.first().is_default is True
        assert versions.first().version_number == 1

    @pytest.mark.django_db()
    def test_switch_default_experiment_version_success(self, experiment):
        save_experiment_version(experiment, is_default=True)
        version1 = ExperimentVersion.objects.get(version_number=1)
        assert version1.is_default is True

        save_experiment_version(experiment, is_default=False)
        version2 = ExperimentVersion.objects.get(version_number=2)
        result = switch_default_experiment_version(experiment, version2.id)

        assert result == "Default version switched successfully"
        assert ExperimentVersion.objects.get(id=version2.id).is_default is True
        assert ExperimentVersion.objects.get(version_number=1).is_default is False

    @pytest.mark.django_db()
    def test_switch_default_experiment_previous_version_non_existent(self, experiment):
        result = switch_default_experiment_version(experiment, 999)
        assert result == "No version found."

    @pytest.mark.django_db()
    def test_switch_default_experiment_new_version_non_existent(self, experiment):
        save_experiment_version(experiment, is_default=True)
        result = switch_default_experiment_version(experiment, 999)
        assert result == "No version found."
        version = ExperimentVersion.objects.get(version_number=1)
        assert version.is_default is True

    @pytest.mark.django_db()
    def test_populate_experiment_version_data_success(self, experiment):
        version_data = populate_experiment_version_data(experiment)
        assert version_data is not None
        assert version_data.name == experiment.name
