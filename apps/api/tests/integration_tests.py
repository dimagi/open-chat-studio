from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment():
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_experiment_response")
def test_create_new_session_and_post_message(mock_response, experiment):
    user = experiment.team.members.first()

    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 200

    experiment_id = response.json()["results"][0]["id"]

    data = {
        "experiment": experiment_id,
        "messages": [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "hello"},
        ],
    }
    response = client.post(reverse("api:session-list"), data=data, format="json")
    response_json = response.json()
    assert response.status_code == 201, response_json
    session_id = response_json["id"]

    mock_response.return_value = "Fido"
    new_message_url = reverse("channels:new_api_message", kwargs={"experiment_id": experiment_id})
    response = client.post(
        new_message_url, data={"message": "What should I call my dog?", "session": session_id}, format="json"
    )
    assert response.status_code == 200, response.json()
    assert response.json() == {"response": "Fido"}
