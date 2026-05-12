"""Tests for the participant read API endpoints (GET /api/participants and GET /api/participants/<id>)."""

import json

import pytest
from django.urls import reverse

from apps.experiments.models import ParticipantData
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_participants(auth_method):
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    experiment2 = ExperimentFactory.create(team=team)

    participant = ParticipantFactory.create(team=team, identifier="user1", platform="api")
    ParticipantData.objects.create(team=team, participant=participant, experiment=experiment, data={"age": 30})
    ParticipantData.objects.create(team=team, participant=participant, experiment=experiment2, data={"role": "admin"})

    user = team.members.first()
    client = ApiTestClient(user, team, auth_method=auth_method)
    response = client.get(reverse("api:update-participant-data"))
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    p = results[0]
    assert p["identifier"] == "user1"
    assert p["platform"] == "api"
    assert len(p["data"]) == 2
    experiment_ids = {str(d["experiment"]) for d in p["data"]}
    assert str(experiment.public_id) in experiment_ids
    assert str(experiment2.public_id) in experiment_ids


@pytest.mark.django_db()
def test_list_participants_filter_by_identifier():
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)

    p1 = ParticipantFactory.create(team=team, identifier="alice", platform="api")
    p2 = ParticipantFactory.create(team=team, identifier="bob", platform="api")
    ParticipantData.objects.create(team=team, participant=p1, experiment=experiment, data={"x": 1})
    ParticipantData.objects.create(team=team, participant=p2, experiment=experiment, data={"x": 2})

    user = team.members.first()
    client = ApiTestClient(user, team)
    url = reverse("api:update-participant-data") + "?identifier=alice"
    response = client.get(url)
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["identifier"] == "alice"


@pytest.mark.django_db()
def test_list_participants_filter_by_platform():
    team = TeamWithUsersFactory.create()

    ParticipantFactory.create(team=team, identifier="u1", platform="api")
    ParticipantFactory.create(team=team, identifier="u2", platform="telegram")

    user = team.members.first()
    client = ApiTestClient(user, team)
    url = reverse("api:update-participant-data") + "?platform=telegram"
    response = client.get(url)
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["platform"] == "telegram"


@pytest.mark.django_db()
def test_list_participants_scoped_to_team():
    team1 = TeamWithUsersFactory.create()
    team2 = TeamWithUsersFactory.create()

    ParticipantFactory.create(team=team1, identifier="t1user", platform="api")
    ParticipantFactory.create(team=team2, identifier="t2user", platform="api")

    user = team1.members.first()
    client = ApiTestClient(user, team1)
    response = client.get(reverse("api:update-participant-data"))
    assert response.status_code == 200
    results = response.json()["results"]
    identifiers = [p["identifier"] for p in results]
    assert "t1user" in identifiers
    assert "t2user" not in identifiers


@pytest.mark.django_db()
def test_retrieve_participant_by_id():
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)

    participant = ParticipantFactory.create(team=team, identifier="alice", platform="api")
    ParticipantData.objects.create(team=team, participant=participant, experiment=experiment, data={"score": 99})

    user = team.members.first()
    client = ApiTestClient(user, team)
    url = reverse("api:participant-detail", kwargs={"id": participant.public_id})
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert data["identifier"] == "alice"
    assert data["id"] == str(participant.public_id)
    assert len(data["data"]) == 1
    assert data["data"][0]["data"] == {"score": 99}
    assert data["data"][0]["experiment"] == str(experiment.public_id)


@pytest.mark.django_db()
def test_retrieve_participant_not_found():
    team = TeamWithUsersFactory.create()
    other_team = TeamWithUsersFactory.create()
    participant = ParticipantFactory.create(team=other_team, identifier="outsider", platform="api")

    user = team.members.first()
    client = ApiTestClient(user, team)
    url = reverse("api:participant-detail", kwargs={"id": participant.public_id})
    response = client.get(url)
    assert response.status_code == 404


@pytest.mark.django_db()
def test_read_only_key_cannot_post_participants():
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    user = team.members.first()
    client = ApiTestClient(user, team, read_only=True)
    data = {
        "identifier": "part1",
        "platform": "api",
        "data": [{"experiment": str(experiment.public_id), "data": {"x": 1}}],
    }
    url = reverse("api:update-participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 403


@pytest.mark.django_db()
def test_read_only_key_can_get_participants():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team, read_only=True)
    response = client.get(reverse("api:update-participant-data"))
    assert response.status_code == 200
