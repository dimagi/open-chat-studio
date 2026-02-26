"""Tests for creating chat sessions with specific chatbot versions."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def authed_user(team_with_users):
    """Return a user who is a member of the experiment's team."""
    return team_with_users.members.first()


@pytest.fixture()
def other_team_user():
    """Return a user from a different team."""
    other_team = TeamWithUsersFactory.create()
    return other_team.members.first()


@pytest.fixture()
def authed_client(authed_user):
    client = APIClient()
    client.login(username=authed_user.email, password="password")
    return client


@pytest.fixture()
def other_team_client(other_team_user):
    client = APIClient()
    client.login(username=other_team_user.email, password="password")
    return client


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def experiment_with_version(experiment):
    """Create an experiment with a published version (version_number=1)."""
    experiment.create_new_version()
    return experiment


@pytest.mark.django_db()
def test_start_session_with_version_number_authenticated(authed_user, authed_client, experiment_with_version):
    """Authenticated users can create sessions for specific published versions."""
    url = reverse("api:chat:start-session")
    version = experiment_with_version.versions.first()
    data = {
        "chatbot_id": str(experiment_with_version.public_id),
        "version_number": version.version_number,
        "participant_remote_id": authed_user.email,
    }
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()
    assert response_json["chatbot"]["version_number"] == version.version_number

    session = ExperimentSession.objects.get(external_id=response_json["session_id"])
    assert session.chat.metadata.get(Chat.MetadataKeys.EXPERIMENT_VERSION) == version.version_number
    # Verify the session's experiment_version property returns the correct version
    assert session.experiment_version.version_number == version.version_number


@pytest.mark.django_db()
def test_start_session_with_version_number_unauthenticated_forbidden(api_client, experiment_with_version):
    """Unauthenticated users cannot specify a version number."""
    url = reverse("api:chat:start-session")
    version = experiment_with_version.versions.first()
    data = {
        "chatbot_id": str(experiment_with_version.public_id),
        "version_number": version.version_number,
    }
    response = api_client.post(url, data=data, format="json")
    assert response.status_code == 403
    assert response.json()["error"] == "Version number requires authentication"


@pytest.mark.django_db()
def test_start_session_with_version_cross_team_forbidden(other_team_user, other_team_client, experiment_with_version):
    """Users from a different team cannot create sessions for another team's chatbot versions."""
    url = reverse("api:chat:start-session")
    version = experiment_with_version.versions.first()
    data = {
        "chatbot_id": str(experiment_with_version.public_id),
        "version_number": version.version_number,
        "participant_remote_id": other_team_user.email,
    }
    response = other_team_client.post(url, data=data, format="json")
    assert response.status_code == 403
    assert response.json()["error"] == "You do not have access to this chatbot"


@pytest.mark.django_db()
def test_start_session_with_nonexistent_version(authed_user, authed_client, experiment_with_version):
    """Requesting a non-existent version returns 404."""
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": str(experiment_with_version.public_id),
        "version_number": 999,
        "participant_remote_id": authed_user.email,
    }
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 404


@pytest.mark.django_db()
def test_start_session_without_version_uses_working(team_with_users, api_client, experiment):
    """Omitting version_number uses the working version (backward compatible)."""
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": str(experiment.public_id)}
    response = api_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()

    session = ExperimentSession.objects.get(external_id=response_json["session_id"])
    assert session.experiment == experiment
    assert Chat.MetadataKeys.EXPERIMENT_VERSION not in session.chat.metadata


@pytest.mark.django_db()
def test_start_session_with_version_id_not_working_version(authed_user, authed_client, experiment_with_version):
    """Using a version's public_id (not the working version) without version_number returns 400."""
    url = reverse("api:chat:start-session")
    version = experiment_with_version.versions.first()
    data = {
        "chatbot_id": str(version.public_id),
        "participant_remote_id": authed_user.email,
    }
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 400
