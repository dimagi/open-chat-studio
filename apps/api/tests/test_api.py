import json

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.models import UserAPIKey
from apps.experiments.models import Participant
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


class TestApiClient(APIClient):
    def __init__(self, user, team):
        super().__init__()
        _user_key, self._api_key = UserAPIKey.objects.create_key(name=f"{user.email}-key", user=user, team=team)

    def request(self, *args, **kwargs):
        kwargs.setdefault(settings.API_KEY_CUSTOM_HEADER, self._api_key)
        return super().request(*args, **kwargs)


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.mark.django_db()
def test_list_experiments(experiment):
    user = experiment.team.members.first()
    client = TestApiClient(user, experiment.team)
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
    client_team_1 = TestApiClient(user, team1)
    client_team_2 = TestApiClient(user, team2)

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
    experiment2 = ExperimentFactory(team=experiment.team)
    participant = Participant.objects.create(identifier=identifier, team=experiment.team)
    user = experiment.team.members.first()
    client = TestApiClient(user, experiment.team)

    # This call should create ParticipantData
    data = {"data": {str(experiment.public_id): {"name": "John"}, str(experiment2.public_id): {"name": "Doe"}}}
    url = reverse("api:update-participant-data", kwargs={"participant_id": participant.public_id})
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    participant_data_exp_1 = experiment.participant_data.get(participant=participant)
    participant_data_exp_2 = experiment2.participant_data.get(participant=participant)
    assert participant_data_exp_1.data["name"] == "John"
    assert participant_data_exp_2.data["name"] == "Doe"

    # Let's update the data
    data["data"][str(experiment.public_id)]["name"] = "Harry"
    client.post(url, json.dumps(data), content_type="application/json")
    participant_data_exp_1.refresh_from_db()
    assert participant_data_exp_1.data["name"] == "Harry"
