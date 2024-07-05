from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_experiment_response")
def test_chat_completion(mock_experiment_response, experiment):
    mock_experiment_response.return_value = "I am fine, thank you."

    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hi, how are you?"},
        ],
    }
    response = client.post(
        reverse("api:openai-chat-completions", kwargs={"experiment_id": experiment.public_id}),
        data=data,
        format="json",
    )
    response_json = response.json()
    assert response.status_code == 200, response_json
    session = ExperimentSession.objects.first()
    assert response_json == {
        "id": session.external_id,
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": "I am fine, thank you.",
            }
        ],
        "created": response_json["created"],
        "model": experiment.llm,
        "object": "chat.completion",
    }
