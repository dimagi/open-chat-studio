import pytest

from apps.channels.datamodels import Attachment
from apps.experiments.tasks import get_response_for_webchat_task
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_get_response_for_webchat_task(session):
    """Basic test for the code in the task. Not intended to test the functions called in the task."""

    file1 = FileFactory(file__data="# a python file\nimport sys", team=session.team)
    file2 = FileFactory(file__data='{"key": "value"}', team=session.team)

    attachments = [
        Attachment(
            file_id=file1.id, type="code_interpreter", name="code.py", size=100, download_link="http://localhost:8000"
        ),
        Attachment(
            file_id=file2.id,
            type="file_search",
            name="file_search.json",
            size=100,
            download_link="http://localhost:8000",
        ),
    ]
    response = get_response_for_webchat_task(
        session.id, session.experiment.id, "Hi", [att.model_dump() for att in attachments]
    )
    assert response["response"] == "Hi"
    assert response["message_id"] is not None
    assert response["error"] is None
