import pytest

from apps.experiments.tasks import async_export_chat
from apps.files.models import File
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_async_export_chat_returns_file_id():
    session = ExperimentSessionFactory()
    result = async_export_chat(session.experiment_id, tags=[], participant=None)
    assert result == {"file_id": File.objects.first().id}
