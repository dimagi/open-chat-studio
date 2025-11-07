from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


@pytest.mark.django_db()
def test_start_chat_session(team_with_users, api_client, experiment):
    url = reverse("api:chat:start-session")
    session_state = {"source": "widget", "page_url": "https://example.com"}
    data = {
        "chatbot_id": experiment.public_id,
        "session_data": session_state,
    }
    response = api_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()
    assert response_json == {
        "session_id": mock.ANY,
        "chatbot": {
            "id": experiment.public_id,
            "name": experiment.name,
            "url": f"http://testserver/api/experiments/{experiment.public_id}/",
            "version_number": 1,
            "versions": [],
        },
        "participant": {"identifier": mock.ANY, "remote_id": ""},
    }
    assert response_json["participant"]["identifier"].startswith("anon:")

    session = ExperimentSession.objects.get(external_id=response_json["session_id"])
    assert session.state == {}  # ignored for anonymous request


@pytest.mark.django_db()
def test_send_message(api_client, session):
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    data = {"message": "hi"}
    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        task.delay.return_value = mock.Mock(task_id="123123")
        response = api_client.post(url, data=data, format="json")
    response_json = response.json()
    assert response_json == {"task_id": "123123", "status": "processing"}


@pytest.mark.django_db()
def test_task_poll(api_client, session):
    url = reverse("api:chat:task-poll-response", kwargs={"session_id": session.external_id, "task_id": "123"})
    response = api_client.get(url)
    response_json = response.json()
    assert response_json == {"message": None, "status": "processing"}


@pytest.mark.django_db()
def test_session_poll(api_client, session):
    url = reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})
    response = api_client.get(url)
    response_json = response.json()
    assert response_json == {"has_more": False, "messages": [], "session_status": "active"}


@pytest.mark.django_db()
def test_session_poll_with_messages(api_client, session):
    messages = ChatMessage.objects.bulk_create(
        [
            ChatMessage(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi"),
            ChatMessage(chat=session.chat, message_type=ChatMessageType.AI, content="Hello", metadata={"test": "123"}),
            ChatMessage(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi again"),
        ]
    )
    messages[-1].create_and_add_tag("test", session.team, "")
    url = reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})
    response = api_client.get(url)
    expected_messages = [
        {
            "attachments": [],
            "content": "Hi",
            "created_at": mock.ANY,
            "metadata": {},
            "role": "user",
            "tags": [],
        },
        {
            "attachments": [],
            "content": "Hello",
            "created_at": mock.ANY,
            "metadata": {"test": "123"},
            "role": "assistant",
            "tags": [],
        },
        {
            "attachments": [],
            "content": "Hi again",
            "created_at": mock.ANY,
            "metadata": {},
            "role": "user",
            "tags": ["test"],
        },
    ]
    assert response.json() == {
        "has_more": False,
        "messages": expected_messages,
        "session_status": "active",
    }

    response = api_client.get(url, data={"limit": 1})
    assert response.json() == {
        "has_more": True,
        "messages": [expected_messages[0]],
        "session_status": "active",
    }


@pytest.skip("This no longer applies to the chat API until we have proper public access implemented.")
@pytest.mark.django_db()
def test_start_chat_session_requires_auth_when_not_public(team_with_users, api_client, experiment):
    url = reverse("api:chat:start-session")
    experiment.participant_allowlist = ["a", "b"]
    experiment.save()
    data = {"chatbot_id": experiment.public_id}
    response = api_client.post(url, data=data, format="json")
    assert response.status_code == 403
