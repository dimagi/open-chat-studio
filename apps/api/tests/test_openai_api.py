import json
import os
from unittest.mock import patch

import pytest
from django.urls import reverse
from openai import OpenAI
from pytest_django.fixtures import live_server_helper

from apps.api.models import UserAPIKey
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.pytest import django_db_with_data
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def live_server(request):
    """
    Function scoped fixture instead of session scoped fixture

    https://github.com/pytest-dev/pytest-django/issues/454

    Notes:
        * The name must be `live_server` since pytest-django uses it to determine the test class type
        * Using this fixture will result in test being a TransactionTestCase.
          See https://docs.djangoproject.com/en/5.0/topics/testing/tools/#transactiontestcase
    """
    addr = request.config.getvalue("liveserver") or os.getenv("DJANGO_LIVE_TEST_SERVER_ADDRESS") or "localhost"

    server = live_server_helper.LiveServer(addr)
    yield server
    server.stop()


@pytest.fixture()
def experiment(team_with_users):
    return ExperimentFactory(team=team_with_users)


@pytest.fixture()
def api_key(team_with_users):
    user = team_with_users.members.first()
    obj, key = UserAPIKey.objects.create_key(
        name=f"{user.get_display_name()} API Key", user=user, team=team_with_users, read_only=False
    )
    return key


@django_db_with_data()
@patch("apps.chat.bots.PipelineBot.process_input")
def test_chat_completion(bot_process_input, experiment, api_key, live_server):
    bot_process_input.return_value = ChatMessage(content="So, this ain't the end, I saw you again today")

    base_url = f"{live_server.url}/api/openai/{experiment.public_id}"

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    completion = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hi, how are you?"},
            {"role": "assistant", "content": "Lekker!"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Sing a song for me"},
                    {"type": "text", "text": "Barracuda"},
                ],
            },
        ],
    )

    session = ExperimentSession.objects.first()
    assert completion.id == session.external_id
    assert completion.model == experiment.llm_provider_model.name
    assert completion.choices[0].message.content == "So, this ain't the end, I saw you again today"
    assert bot_process_input.call_args_list[0][0] == ("Sing a song for me Barracuda",)
    assert [(m.message_type, m.content) for m in session.chat.messages.all()] == [
        ("system", "You are a helpful assistant."),
        ("human", "Hi, how are you?"),
        ("ai", "Lekker!"),
        ("human", "Sing a song for me Barracuda"),
    ]


@pytest.mark.django_db()
def test_unsupported_message_type(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    url = reverse("api:openai-chat-completions", kwargs={"experiment_id": experiment.public_id})
    data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "selfie!"},
                    {"type": "image_url", "image_url": "https://example.com/image.jpg"},
                ],
            },
        ]
    }
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": None,
            "message": "Only text messages are supported",
            "param": None,
            "type": "error",
        }
    }
