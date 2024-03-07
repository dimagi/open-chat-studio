import pytest

from apps.files.models import File
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
def test_deleting_experiment_with_files():
    experiment = ExperimentFactory()
    files = FileFactory.create_batch(3)
    experiment.files.set(files)

    experiment.delete()

    assert File.objects.count() == 0


@pytest.mark.django_db()
def test_deleting_experiment_with_files_multiple_references(caplog):
    experiment = ExperimentFactory()
    files = FileFactory.create_batch(3)
    experiment.files.set(files)

    experiment2 = ExperimentFactory()
    experiment2.files.set(files[:1])

    experiment.delete()

    remaining = File.objects.all()
    assert len(remaining) == 1
    assert remaining[0] == files[0]
    assert str(files[0].id) in caplog.text
