import uuid
from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory

TEST_SESSION_ID = str(uuid.uuid4())


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


@pytest.fixture()
def mock_task_response():
    """Mock the get_message_task_response function to return controlled test data."""
    with mock.patch("apps.api.views.chat.get_message_task_response") as mock_func:
        yield mock_func


@pytest.mark.django_db()
def test_chat_poll_task_response_with_file_attachments(api_client, session, mock_task_response):
    """Test that file attachments in messages are properly serialized with request context."""
    # Create a test file
    test_file = FileFactory(team=session.chat.team)

    attachment = session.chat.attachments.create(tool_type="code_interpreter")
    attachment.files.add(test_file)

    # Add message with a reference to both the chat and assistant level files
    metadata = {
        "ocs_attachment_file_ids": [test_file.id],
    }
    message = ChatMessage.objects.create(chat=session.chat, message_type="ai", content="Hi", metadata=metadata)

    # Mock the get_attached_files method to return our test file
    mock_task_response.return_value = {"complete": True, "error_msg": None, "message": message}

    url = reverse("api:chat:task-poll-response", kwargs={"session_id": session.external_id, "task_id": "test-task-124"})

    response = api_client.get(url)

    assert response.status_code == 200
    response_data = response.json()

    assert response_data["status"] == "complete"
    assert "message" in response_data
    attachments = response_data["message"]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["name"] == test_file.name
    assert attachments[0]["content_url"].endswith(f"/api/files/{test_file.id}/content")


@pytest.fixture()
def mock_session():
    """Mock session lookup to avoid DB fixture issues."""
    mock_sess = mock.Mock()
    mock_sess.experiment.name = "TestBot"
    mock_sess.experiment.description = "A test bot"
    mock_sess.experiment.is_public = True
    with (
        mock.patch("apps.api.views.chat.get_experiment_session_cached", return_value=mock_sess),
        mock.patch("apps.api.permissions.get_experiment_session_cached", return_value=mock_sess),
    ):
        yield mock_sess


@pytest.mark.django_db()
def test_chat_poll_task_response_empty_dict(api_client, mock_session, mock_task_response):
    """When get_message_task_response returns {} (skip_render), respond with processing status."""
    mock_task_response.return_value = {}

    url = reverse("api:chat:task-poll-response", kwargs={"session_id": TEST_SESSION_ID, "task_id": "test-task-1"})
    response = api_client.get(url)

    assert response.status_code == 200
    assert response.json() == {"status": "processing"}


@pytest.mark.django_db()
@mock.patch("apps.api.views.chat.get_progress_message")
def test_chat_poll_task_response_processing_with_progress(mock_progress, api_client, mock_session, mock_task_response):
    """When task is still processing, include progress message if available."""
    mock_task_response.return_value = {"complete": False, "error_msg": None, "message": None}
    mock_progress.return_value = "Thinking..."

    url = reverse("api:chat:task-poll-response", kwargs={"session_id": TEST_SESSION_ID, "task_id": "test-task-2"})
    response = api_client.get(url)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert data["message"] == "Thinking..."
