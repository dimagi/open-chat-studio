import os
from unittest.mock import patch

import pytest
from openai import OpenAI
from pytest_django.fixtures import live_server_helper

from apps.api.models import UserAPIKey
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentFactory


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
    obj, key = UserAPIKey.objects.create_key(name=f"{user.get_display_name()} API Key", user=user, team=team_with_users)
    return key


@pytest.mark.django_db(
    # Needed to provide cascaded rollback for the testdata. I'm not certain which apps are necessary but these
    # seem to work.
    # See https://docs.djangoproject.com/en/5.0/topics/testing/advanced/#django.test.TransactionTestCase.available_apps
    available_apps=["apps.api", "apps.experiments", "apps.teams", "apps.users"],
    serialized_rollback=True,
)
@patch("apps.chat.channels.ApiChannel._get_bot_response")
def test_chat_completion(mock_experiment_response, experiment, api_key, live_server):
    mock_experiment_response.return_value = "I am fine, thank you."

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
        ],
    )

    assert ExperimentSession.objects.count() == 1
    assert completion.id == ExperimentSession.objects.first().external_id
    assert completion.model == experiment.llm_provider_model.name
    assert completion.choices[0].message.content == "I am fine, thank you."
