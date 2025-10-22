"""Tests for authenticated access to the Chat API.

When access is authenticated, it implies that the chat widget is being hosted on the same
OCS instance as the bot (to allow the session cookie to work). In this case, we should enforce
that the `remote_id` matches the authenticated user's email address.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.experiments.models import ExperimentSession, Participant, ParticipantData
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def authed_user():
    other_team = TeamWithUsersFactory.create()
    return other_team.members.first()


@pytest.fixture()
def authed_client(authed_user):
    client = APIClient()
    client.login(username=authed_user.email, password="password")
    return client


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


@pytest.mark.django_db()
def test_start_chat_session_with_auth(authed_user, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "participant_remote_id": authed_user.email}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()
    assert response_json["participant"]["identifier"] == authed_user.email


@pytest.mark.django_db()
def test_start_chat_session_with_auth_requires_remote_id(authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 400


@pytest.mark.django_db()
def test_start_chat_session_with_auth_requires_remote_id_to_match_user(authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "participant_remote_id": "not the user's email"}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 400


@pytest.mark.django_db()
def test_start_chat_session_with_session_state(authed_user, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": experiment.public_id,
        "session_data": {"ref": "123"},
        "participant_remote_id": authed_user.email,
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
    authed_user, authed_client, experiment, participant_remote_id, status_code
):
    from apps.experiments.const import ParticipantAccessLevel
    url = reverse("api:chat:start-session")
    experiment.participant_access_level = ParticipantAccessLevel.ALLOW_LIST
    experiment.participant_allowlist = [authed_user.email]
    experiment.save()
    data = {"chatbot_id": experiment.public_id}
    if participant_remote_id is not None:
        data["participant_remote_id"] = participant_remote_id
    if participant_remote_id == "user_email":
        data["participant_remote_id"] = authed_user.email
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == status_code


@pytest.mark.django_db()
def test_start_chat_session_with_name(authed_user, authed_client, experiment):
    url = reverse("api:chat:start-session")
    name = "John Doe"
    session_state = {"ref": "abc123"}

    data = {
        "chatbot_id": experiment.public_id,
        "participant_remote_id": authed_user.email,
        "participant_name": name,
        "session_data": session_state,
    }

    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()

    participant = Participant.objects.get(identifier=response_json["participant"]["identifier"])
    assert response_json["participant"]["identifier"] == authed_user.email

    participant_data = ParticipantData.objects.get(participant=participant, experiment=experiment)
    assert participant_data.data.get("name") == name

    session = ExperimentSession.objects.get(external_id=response_json["session_id"])
    assert session.state == session_state
