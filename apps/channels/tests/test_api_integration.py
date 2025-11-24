from unittest.mock import Mock, patch

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession
from apps.participants.models import Participant

# TODO: Update Participant import
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

from .message_examples import api_messages


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_bot_response")
def test_new_message_creates_a_channel_and_participant(get_llm_response_mock, experiment, client):
    get_llm_response_mock.return_value = ChatMessage(content="Hi user")

    channels_queryset = ExperimentChannel.objects.filter(team=experiment.team, platform=ChannelPlatform.API)
    assert not channels_queryset.exists()

    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"response": "Hi user", "attachments": []}
    channels = channels_queryset.all()
    assert len(channels) == 1
    participant = Participant.objects.get(identifier=user.email, team=experiment.team, user=user)
    assert ExperimentSession.objects.filter(
        experiment=experiment, experiment_channel=channels[0], participant=participant
    ).exists()


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._load_latest_session")
@patch("apps.chat.channels.ApiChannel._get_bot_response")
def test_new_message_with_existing_session(get_llm_response_mock, _load_latest_session, experiment, client):
    get_llm_response_mock.return_value = ChatMessage(content="Hi user")

    user = experiment.team.members.first()
    participant, _ = Participant.objects.get_or_create(
        identifier=user.email, team=experiment.team, user=user, platform="api"
    )
    channel = ExperimentChannel.objects.get_team_api_channel(experiment.team)
    session = ExperimentSessionFactory(experiment=experiment, participant=participant, experiment_channel=channel)

    client = ApiTestClient(user, experiment.team)

    response = client.post(
        reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
        api_messages.text_message(session_id=str(session.external_id)),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"response": "Hi user", "attachments": []}

    # check that no new sessions were created
    assert not ExperimentSession.objects.exclude(id=session.id).exists()
    _load_latest_session.assert_not_called()


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


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_bot_response")
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

    mock_response.return_value = ChatMessage(content="Fido")
    new_message_url = reverse("channels:new_api_message", kwargs={"experiment_id": experiment_id})
    response = client.post(
        new_message_url, data={"message": "What should I call my dog?", "session": session_id}, format="json"
    )
    assert response.status_code == 200, response.json()
    assert response.json() == {"response": "Fido", "attachments": []}


@pytest.mark.django_db()
@patch("apps.chat.channels.ApiChannel._get_bot_response")
def test_attachments_returned(mock_response, experiment):
    user = experiment.team.members.first()

    session = ExperimentSessionFactory()
    file = FileFactory()
    mock_chat_message = Mock(spec=ChatMessage, chat=session.chat, content="Fido")
    mock_chat_message.get_attached_files.return_value = [file]
    mock_response.return_value = mock_chat_message

    client = ApiTestClient(user, experiment.team)

    new_message_url = reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id})
    response = client.post(new_message_url, data={"message": "What should I call my dog?"}, format="json")

    assert response.json() == {
        "response": "Fido",
        "attachments": [{"file_name": file.name, "link": file.download_link(session.id)}],
    }


@pytest.mark.django_db()
def test_read_only_key_cannot_post(experiment):
    user = experiment.team.members.first()

    client = ApiTestClient(user, experiment.team, read_only=True)
    new_message_url = reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id})
    response = client.post(new_message_url, data={"message": "What should I call my dog?"}, format="json")
    assert response.status_code == 403
