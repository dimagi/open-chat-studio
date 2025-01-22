import pytest

from apps.experiments.tasks import get_response_for_webchat_task
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_get_response_for_webchat_task(session):
    """Basic test for the code in the task. Not intended to test the functions called in the task."""
    with mock_experiment_llm(None, responses=["how can I help?"]):
        response = get_response_for_webchat_task(
            session.id,
            session.experiment.id,
            "Hi",
            [
                {"file_id": 1, "type": "code_interpreter"},
                {"file_id": 2, "type": "file_search"},
            ],
        )
    assert response["response"] == "how can I help?"
    assert response["message_id"] is not None
    assert response["error"] is None
