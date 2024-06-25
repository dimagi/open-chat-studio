from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession, Participant
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.langchain import FakeLlm, FakeLlmService
from apps.utils.tests.clients import ApiTestClient

from .message_examples import api_messages


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


def fake_llm():
    return FakeLlm(responses=[["Hi", " there!"]], token_counts=[30, 20, 10])


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_llm_response")
def test_new_message_creates_a_channel_and_session(get_llm_response_mock, experiment, client):
    get_llm_response_mock.return_value = "Hi user"
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(session_id=None),
        content_type="application/json",
    )

    assert response.status_code == 200
    session = ExperimentSession.objects.last()
    assert response.json() == {"response": "Hi user", "session_id": str(session.external_id)}
    assert ExperimentChannel.objects.filter(experiment=experiment, platform=ChannelPlatform.API).exists() is True
    assert Participant.objects.filter(identifier=user.email, team=experiment.team).exists()


@pytest.mark.django_db()
@patch("apps.service_providers.llm_service.runnables.SimpleExperimentRunnable.participant_data")
@patch("apps.experiments.models.Experiment.get_llm_service", return_value=FakeLlmService(llm=fake_llm()))
def test_chat_to_specified_session(get_llm_service, participant_data, experiment, client):
    participant_data.return_value = {}
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(session_id=None),
        content_type="application/json",
    )
    session_id = response.json()["session_id"]

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(session_id=session_id),
        content_type="application/json",
    )

    assert response.json()["session_id"] == session_id

    assert ExperimentChannel.objects.filter(experiment=experiment, platform=ChannelPlatform.API).count() == 1
    assert ExperimentSession.objects.count() == 1
    assert ChatMessage.objects.filter(chat__experiment_session__external_id=session_id).count() == 4

    # Let's start a new session and chat to it
    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(session_id=None),
        content_type="application/json",
    )

    session_id = response.json()["session_id"]
    assert ChatMessage.objects.filter(chat__experiment_session__external_id=session_id).count() == 2
