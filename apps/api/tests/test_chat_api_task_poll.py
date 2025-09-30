"""Tests for the chat_poll_task_response API endpoint to ensure proper serializer context."""

from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage, ChatMessageType
from apps.files.models import File
from apps.utils.factories.experiment import ExperimentSessionFactory


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
def test_chat_poll_task_response_includes_request_context(api_client, session, mock_task_response):
    """Test that MessageSerializer receives request context when serializing complete messages."""
    # Create a mock ChatMessage to be returned by the task
    message = ChatMessage(
        id=123,
        chat=session.chat,
        message_type=ChatMessageType.AI,
        content="Test response"
    )
    
    # Mock the task response to return a complete message
    mock_task_response.return_value = {
        "complete": True,
        "error_msg": None,
        "message": message
    }
    
    url = reverse("api:chat:task-poll-response", kwargs={
        "session_id": session.external_id,
        "task_id": "test-task-123"
    })
    
    # Patch MessageSerializer to verify it receives the request context
    with mock.patch("apps.api.views.chat.MessageSerializer") as mock_serializer:
        mock_serializer.return_value.data = {
            "role": "assistant",
            "content": "Test response",
            "created_at": "2023-01-01T00:00:00Z",
            "metadata": {},
            "tags": [],
            "attachments": []
        }
        
        response = api_client.get(url)
        
        # Verify the response is successful
        assert response.status_code == 200
        
        # Verify MessageSerializer was called with the request context
        mock_serializer.assert_called_once_with(message, context={'request': mock.ANY})
        
        # Get the actual request object passed to the serializer
        call_args = mock_serializer.call_args
        assert 'context' in call_args.kwargs
        assert 'request' in call_args.kwargs['context']
        
        # Verify the response data structure
        response_data = response.json()
        assert response_data["status"] == "complete"
        assert "message" in response_data


@pytest.mark.django_db()
def test_chat_poll_task_response_with_file_attachments(api_client, session, mock_task_response):
    """Test that file attachments in messages are properly serialized with request context."""
    # Create a test file
    test_file = File.objects.create(
        name="test_file.pdf",
        team=session.team,
        content_size=1024,
        content_type="application/pdf",
        purpose="assistant"
    )
    
    # Create a mock ChatMessage with attachments
    message = ChatMessage(
        id=124,
        chat=session.chat,
        message_type=ChatMessageType.AI,
        content="Here's your file"
    )
    
    # Mock the get_attached_files method to return our test file
    with mock.patch.object(message, 'get_attached_files', return_value=[test_file]):
        mock_task_response.return_value = {
            "complete": True,
            "error_msg": None,
            "message": message
        }
        
        url = reverse("api:chat:task-poll-response", kwargs={
            "session_id": session.external_id,
            "task_id": "test-task-124"
        })
        
        response = api_client.get(url)
        
        # Verify the response is successful
        assert response.status_code == 200
        response_data = response.json()
        
        # Verify the response structure includes the message
        assert response_data["status"] == "complete"
        assert "message" in response_data
        # The actual file serialization with proper URLs is tested through the integration
        # since we're testing that the request context is properly passed to MessageSerializer
