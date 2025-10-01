from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


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
