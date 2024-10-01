import json
import uuid

import pytest
from dateutil.relativedelta import relativedelta
from django.urls import reverse
from django.utils import timezone

from apps.experiments.models import Participant
from apps.teams.backends import EXPERIMENT_ADMIN_GROUP, add_user_to_team
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.mark.django_db()
def test_list_experiments(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 200
    expected_json = {
        "results": [
            {
                "name": experiment.name,
                "id": experiment.public_id,
                "url": f"http://testserver/api/experiments/{experiment.public_id}/",
                "version_number": 0,
            }
        ],
        "next": None,
        "previous": None,
    }
    assert response.json() == expected_json


@pytest.mark.django_db()
def test_retrieve_experiments(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:experiment-detail", kwargs={"id": experiment.public_id}))
    assert response.status_code == 200
    assert response.json() == {
        "id": experiment.public_id,
        "name": experiment.name,
        "url": f"http://testserver/api/experiments/{experiment.public_id}/",
        "version_number": 0,
    }


@pytest.mark.django_db()
def test_only_experiments_from_the_scoped_team_is_returned():
    experiment_team_1 = ExperimentFactory(team=TeamWithUsersFactory(member__user__username="uname1"))
    experiment_team_2 = ExperimentFactory(team=TeamWithUsersFactory(member__user__username="uname2"))
    team1 = experiment_team_1.team
    team2 = experiment_team_2.team

    user = team1.members.first()
    add_user_to_team(team2, user, [EXPERIMENT_ADMIN_GROUP])

    client_team_1 = ApiTestClient(user, team1)
    client_team_2 = ApiTestClient(user, team2)

    # Fetch experiments from team 1
    response = client_team_1.get(reverse("api:experiment-list"))
    experiments = response.json()["results"]
    assert len(experiments) == 1
    assert experiments[0]["id"] == experiment_team_1.public_id

    # Fetch experiments from team 2
    response = client_team_2.get(reverse("api:experiment-list"))
    experiments = response.json()["results"]
    assert len(experiments) == 1
    assert experiments[0]["id"] == experiment_team_2.public_id


@pytest.mark.django_db()
def test_create_and_update_participant_data():
    identifier = "part1"
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    experiment2 = ExperimentFactory(team=experiment.team)
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    # This call should create ParticipantData
    data = {
        "identifier": identifier,
        "platform": "api",
        "data": [
            {"experiment": str(experiment.public_id), "data": {"name": "John"}},
            {"experiment": str(experiment2.public_id), "data": {"name": "Doe"}},
        ],
    }
    url = reverse("api:update-participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    participant = Participant.objects.get(identifier=identifier)
    assert participant.name == ""
    participant_data_exp_1 = experiment.participant_data.get(participant__identifier=identifier)
    participant_data_exp_2 = experiment2.participant_data.get(participant__identifier=identifier)
    assert participant_data_exp_1.data["name"] == "John"
    assert participant_data_exp_2.data["name"] == "Doe"

    # Let's update the data
    data["data"] = [{"experiment": str(experiment.public_id), "data": {"name": "Harry"}}]
    data["name"] = "Bob"
    client.post(url, json.dumps(data), content_type="application/json")
    participant_data_exp_1.refresh_from_db()
    assert participant_data_exp_1.data["name"] == "Harry"
    participant.refresh_from_db()
    assert participant.name == "Bob"


@pytest.mark.django_db()
def test_update_participant_data_returns_404():
    """A 404 will be returned when any experiment in the payload is not found. No updates should occur"""
    identifier = "part1"
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    experiment2 = ExperimentFactory(team=TeamWithUsersFactory())
    participant = Participant.objects.create(identifier=identifier, team=experiment.team, platform="api")
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    # This call should create ParticipantData for team 1's experiment only
    data = {
        "identifier": participant.identifier,
        "platform": participant.platform,
        "data": [
            {"experiment": str(experiment.public_id), "data": {"name": "John"}},
            {"experiment": str(experiment2.public_id), "data": {"name": "Doe"}},
        ],
    }
    url = reverse("api:update-participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 404
    assert response.json() == {"errors": [{"message": f"Experiment {experiment2.public_id} not found"}]}
    # Assert that nothing was created
    assert experiment.participant_data.filter(participant=participant).exists() is False


@pytest.mark.django_db()
def test_create_participant_schedules(experiment):
    identifier = "part1"
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    # This call should create ParticipantData
    trigger_date1 = timezone.now() + relativedelta(hours=1)
    trigger_date2 = timezone.now() + relativedelta(days=1)
    data = {
        "identifier": identifier,
        "platform": "api",
        "data": [
            {
                "experiment": str(experiment.public_id),
                "data": {"name": "John"},
                "schedules": [
                    {"name": "schedule1", "prompt": "tell ET to phone home", "date": trigger_date1.isoformat()},
                    {"name": "schedule2", "prompt": "email john", "date": trigger_date2.isoformat()},
                ],
            },
        ],
    }
    url = reverse("api:update-participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    participant_data_exp_1 = experiment.participant_data.get(participant__identifier=identifier)
    assert participant_data_exp_1.data["name"] == "John"
    schedules = list(
        experiment.scheduled_messages.filter(participant__identifier=identifier).order_by("next_trigger_date")
    )
    assert len(schedules) == 2
    assert schedules[0].custom_schedule_params == {
        "name": "schedule1",
        "prompt_text": "tell ET to phone home",
        "repetitions": 1,
        "frequency": 1,
        "time_period": "days",
    }
    assert schedules[0].next_trigger_date == trigger_date1

    assert schedules[1].custom_schedule_params == {
        "name": "schedule2",
        "prompt_text": "email john",
        "repetitions": 1,
        "frequency": 1,
        "time_period": "days",
    }
    assert schedules[1].next_trigger_date == trigger_date2
    return schedules


@pytest.mark.django_db()
def test_update_participant_schedules(experiment):
    schedules = test_create_participant_schedules(experiment)

    identifier = "part1"
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    trigger_date3 = timezone.now() + relativedelta(days=1)
    data = {
        "identifier": identifier,
        "platform": "api",
        "data": [
            {
                "experiment": str(experiment.public_id),
                "schedules": [
                    # Create a new schedule
                    {
                        "id": uuid.uuid4().hex,
                        "name": "schedule3",
                        "prompt": "don't forget to floss",
                        "date": trigger_date3.isoformat(),
                    },
                    # delete one we created before
                    {"id": schedules[0].external_id, "delete": True},
                    # update another one
                    {
                        "id": schedules[1].external_id,
                        "name": "schedule2 updated",
                        "prompt": "email john smith",
                        "date": schedules[1].next_trigger_date.isoformat(),
                    },
                ],
            },
        ],
    }
    url = reverse("api:update-participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    # make sure the data hasn't changed
    participant_data = experiment.participant_data.get(participant__identifier=identifier)
    assert participant_data.data["name"] == "John"

    updated_schedules = list(
        experiment.scheduled_messages.filter(participant__identifier=identifier).order_by("next_trigger_date")
    )
    assert len(updated_schedules) == 2
    assert updated_schedules[0].custom_schedule_params == {
        "name": "schedule2 updated",
        "prompt_text": "email john smith",
        "repetitions": 1,
        "frequency": 1,
        "time_period": "days",
    }
    assert updated_schedules[0].next_trigger_date == schedules[1].next_trigger_date

    assert updated_schedules[1].custom_schedule_params == {
        "name": "schedule3",
        "prompt_text": "don't forget to floss",
        "repetitions": 1,
        "frequency": 1,
        "time_period": "days",
    }
    assert updated_schedules[1].next_trigger_date == trigger_date3
