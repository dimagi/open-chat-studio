from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.models import UserAPIKey
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Participant
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory

from .message_examples import api_messages


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


def _get_client_for_user(user, team):
    client = APIClient()
    _user_key, api_key = UserAPIKey.objects.create_key(name="Test", user=user, team=team)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {api_key}")
    return client


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_llm_response")
def test_new_message_creates_a_channel(get_llm_response_mock, experiment, client):
    get_llm_response_mock.return_value = "Hi user"
    user = experiment.team.members.first()
    client = _get_client_for_user(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message"),
        api_messages.text_message(experiment.public_id),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"response": "Hi user"}
    assert ExperimentChannel.objects.filter(experiment=experiment, platform=ChannelPlatform.API).exists() is True
    assert Participant.objects.filter(identifier=user.email, team=experiment.team).exists()
