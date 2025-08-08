"""Tests for authenticated access to the Chat API.

When access is authenticated, it implies that the chat widget is being hosted on the same
OCS instance as the bot (to allow the session cookie to work). In this case, we should enforce
that the `remote_id` matches the authenticated user's email address.
"""

import pytest
from django.urls import reverse

from apps.experiments.models import ExperimentSession, Participant, ParticipantData
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def authed_client():
    other_team = TeamWithUsersFactory.create()
    user = other_team.members.first()
    client = ApiTestClient(user, other_team)
    return client


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


@pytest.mark.django_db()
def test_start_chat_session_with_auth(team_with_users, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "participant_remote_id": authed_client.user.email}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()
    assert response_json["participant"]["identifier"] == authed_client.user.email


@pytest.mark.django_db()
def test_start_chat_session_with_auth_requires_remote_id(team_with_users, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 400


@pytest.mark.django_db()
def test_start_chat_session_with_auth_requires_remote_id_to_match_user(team_with_users, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "participant_remote_id": "not the user's email"}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 400


@pytest.mark.django_db()
def test_start_chat_session_with_session_state(team_with_users, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": experiment.public_id,
        "session_data": {"ref": "123"},
        "participant_remote_id": authed_client.user.email,
    }
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert session.state == {"ref": "123"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("participant_remote_id", "status_code"), [(None, 400), ("", 400), ("123", 400), ("user_email", 201)]
)
def test_start_chat_session_requires_auth_when_not_public(
    team_with_users, authed_client, experiment, participant_remote_id, status_code
):
    url = reverse("api:chat:start-session")
    experiment.participant_allowlist = [authed_client.user.email]
    experiment.save()
    data = {"chatbot_id": experiment.public_id}
    if participant_remote_id is not None:
        data["participant_remote_id"] = participant_remote_id
    if participant_remote_id == "user_email":
        data["participant_remote_id"] = authed_client.user.email
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == status_code


@pytest.mark.django_db()
def test_start_chat_session_with_name(team_with_users, authed_client, experiment):
    url = reverse("api:chat:start-session")
    name = "John Doe"
    session_state = {"ref": "abc123"}

    data = {
        "chatbot_id": experiment.public_id,
        "participant_remote_id": authed_client.user.email,
        "participant_name": name,
        "session_data": session_state,
    }

    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()

    participant = Participant.objects.get(identifier=response_json["participant"]["identifier"])
    assert response_json["participant"]["identifier"] == authed_client.user.email

    participant_data = ParticipantData.objects.get(participant=participant, experiment=experiment, team=team_with_users)
    assert participant_data.data.get("name") == name

    session = ExperimentSession.objects.get(external_id=response_json["session_id"])
    assert session.state == session_state
