import json

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.models import UserAPIKey
from apps.experiments.models import Participant, ParticipantData
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


def _get_client_for_user(user, team):
    client = APIClient()
    _user_key, api_key = UserAPIKey.objects.create_key(name="Test", user=user, team=team)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {api_key}")
    return client


@pytest.mark.django_db()
def test_list_experiments(experiment):
    user = experiment.team.members.first()
    client = _get_client_for_user(user, experiment.team)
    response = client.get(reverse("api:list-experiments"))
    assert response.status_code == 200
    expected_json = [{"name": experiment.name, "experiment_id": experiment.public_id}]
    assert response.json() == expected_json


@pytest.mark.django_db()
def test_only_experiments_from_the_scoped_team_is_returned():
    experiment_team_1 = ExperimentFactory(team=TeamWithUsersFactory())
    experiment_team_2 = ExperimentFactory(team=TeamWithUsersFactory())
    team1 = experiment_team_1.team
    team2 = experiment_team_2.team
    user = team1.members.first()
    client_team_1 = _get_client_for_user(user, team1)
    client_team_2 = _get_client_for_user(user, team2)

    # Fetch experiments from team 1
    response = client_team_1.get(reverse("api:list-experiments"))
    experiments = response.json()
    assert len(experiments) == 1
    assert experiments[0]["experiment_id"] == experiment_team_1.public_id

    # Fetch experiments from team 2
    response = client_team_2.get(reverse("api:list-experiments"))
    experiments = response.json()
    assert len(experiments) == 1
    assert experiments[0]["experiment_id"] == experiment_team_2.public_id


@pytest.mark.django_db()
def test_update_participant_data_creats_new_record():
    identifier = "part1"
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    participant = Participant.objects.create(identifier=identifier, team=experiment.team)
    user = experiment.team.members.first()
    client = _get_client_for_user(user, experiment.team)

    # This call should create ParticipantData
    data = {"participant_id": identifier, "experiment_id": experiment.public_id, "details": {"name": "John"}}
    client.post(reverse("api:update-participant-data"), json.dumps(data), content_type="application/json")

    participant_data = ParticipantData.objects.get(participant=participant)
    experiment.refresh_from_db()
    assert experiment.participant_data.filter(participant=participant).exists() is True
    assert participant_data.data["name"] == "John"

    # Let's update the data
    data["details"]["name"] = "Harry"
    client.post(reverse("api:update-participant-data"), json.dumps(data), content_type="application/json")
    participant_data.refresh_from_db()
    assert participant_data.data["name"] == "Harry"
