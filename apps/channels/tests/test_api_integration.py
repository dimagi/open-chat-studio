from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Participant
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

from .message_examples import api_messages


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_llm_response")
def test_new_message_creates_a_channel(get_llm_response_mock, experiment, client):
    get_llm_response_mock.return_value = "Hi user"
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"response": "Hi user"}
    assert ExperimentChannel.objects.filter(experiment=experiment, platform=ChannelPlatform.API).exists() is True
    assert Participant.objects.filter(identifier=user.email, team=experiment.team).exists()
