from unittest.mock import patch

import pytest

from apps.channels.datamodels import Attachment
from apps.experiments.tasks import get_response_for_webchat_task
from apps.pipelines.exceptions import MessageTooLargeError
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory.create()


@pytest.mark.django_db()
def test_get_response_for_webchat_task(session):
    """Basic test for the code in the task. Not intended to test the functions called in the task."""

    file1 = FileFactory.create(file__data="# a python file\nimport sys", team=session.team)
    file2 = FileFactory.create(file__data='{"key": "value"}', team=session.team)

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


@pytest.mark.django_db()
def test_message_too_large_sets_user_facing_error(session):
    with patch("apps.experiments.tasks.WebChannel.new_user_message", side_effect=MessageTooLargeError("too big")):
        response = get_response_for_webchat_task(session.id, session.experiment.id, "Hi")

    assert response["error"] == "too big"
    assert response["user_facing_error"] is True
    assert response["response"] is None


@pytest.mark.django_db()
def test_generic_exception_sets_error_without_user_facing_flag(session):
    with patch("apps.experiments.tasks.WebChannel.new_user_message", side_effect=RuntimeError("boom")):
        response = get_response_for_webchat_task(session.id, session.experiment.id, "Hi")

    assert response["error"] == "boom"
    assert not response.get("user_facing_error")
    assert response["response"] is None
