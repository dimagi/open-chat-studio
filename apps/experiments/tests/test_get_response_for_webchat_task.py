import pytest

from apps.channels.datamodels import Attachment
from apps.experiments.tasks import get_response_for_webchat_task
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_llm


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_get_response_for_webchat_task(session):
    """Basic test for the code in the task. Not intended to test the functions called in the task."""

    attachments = [
        Attachment(file_id=1, type="code_interpreter", name="code.py", size=100, download_link="http://localhost:8000"),
        Attachment(
            file_id=2, type="file_search", name="file_search.json", size=100, download_link="http://localhost:8000"
        ),
    ]
    with mock_llm(responses=["how can I help?"]):
        response = get_response_for_webchat_task(
            session.id, session.experiment.id, "Hi", [att.model_dump() for att in attachments]
        )
    assert response["response"] == "how can I help?"
    assert response["message_id"] is not None
    assert response["error"] is None
