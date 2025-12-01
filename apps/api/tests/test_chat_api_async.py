from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncClient
from django.urls import reverse

from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def api_client():
    return AsyncClient()


@pytest.mark.asyncio()
@pytest.mark.django_db()
async def test_start_chat_session(team_with_users, api_client):
    session = await sync_to_async(ExperimentSessionFactory, thread_sensitive=True)()
    experiment = session.experiment
    url = reverse("api:chat:start-session-async")
    session_state = {"source": "widget", "page_url": "https://example.com"}
    data = {
        "chatbot_id": experiment.public_id,
        "session_data": session_state,
    }
    response = await api_client.post(url, data=data, format="json")
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

    session = await ExperimentSession.objects.aget(external_id=response_json["session_id"])
    assert session.state == {}  # ignored for anonymous request


@pytest.mark.asyncio()
@pytest.mark.django_db()
async def test_send_message(api_client):
    session = await sync_to_async(ExperimentSessionFactory, thread_sensitive=True)()
    url = reverse("api:chat:send-message-async", kwargs={"session_id": session.external_id})
    data = {"message": "hi"}
    response = await api_client.post(url, data=data, format="json")
    response_json = response.json()
    assert response_json == {"message": "hi"}
