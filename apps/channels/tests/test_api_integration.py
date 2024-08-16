from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import ExperimentSession, Participant
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

from ...utils.factories.channels import ExperimentChannelFactory
from .message_examples import api_messages


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_bot_response")
def test_new_message_creates_a_channel_and_participant(get_llm_response_mock, experiment, client):
    get_llm_response_mock.return_value = "Hi user"

    channels_queryset = ExperimentChannel.objects.filter(experiment=experiment, platform=ChannelPlatform.API)
    assert not channels_queryset.exists()

    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"response": "Hi user"}
    channels = channels_queryset.all()
    assert len(channels) == 1
    participant = Participant.objects.get(identifier=user.email, team=experiment.team, user=user)
    assert ExperimentSession.objects.filter(
        experiment=experiment, experiment_channel=channels[0], participant=participant
    ).exists()


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_latest_session")
@patch("apps.chat.channels.ApiChannel._get_bot_response")
def test_new_message_with_existing_session(get_llm_response_mock, _get_latest_session, experiment, client):
    get_llm_response_mock.return_value = "Hi user"

    user = experiment.team.members.first()
    participant, _ = Participant.objects.get_or_create(
        identifier=user.email, team=experiment.team, user=user, platform="api"
    )
    channel = ExperimentChannelFactory(platform=ChannelPlatform.API, experiment=experiment)
    session = ExperimentSessionFactory(experiment=experiment, participant=participant, experiment_channel=channel)

    client = ApiTestClient(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(session_id=str(session.external_id)),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"response": "Hi user"}

    # check that no new sessions were created
    assert not ExperimentSession.objects.exclude(id=session.id).exists()
    _get_latest_session.assert_not_called()


@pytest.mark.django_db()
def test_new_message_to_another_users_session(experiment, client):
    users = experiment.team.members.all()
    session_user = users[1]
    participant, _ = Participant.objects.get_or_create(
        identifier=session_user.email, team=experiment.team, user=session_user, platform="api"
    )
    session = ExperimentSessionFactory(experiment=experiment, participant=participant)

    auth_user = users[0]
    client = ApiTestClient(auth_user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(session_id=str(session.external_id)),
        content_type="application/json",
    )
    assert response.status_code == 404
