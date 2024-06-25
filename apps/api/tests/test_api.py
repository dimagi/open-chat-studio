import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.experiments.models import ExperimentSession, Participant
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.langchain import FakeLlm, FakeLlmService
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


def fake_llm():
    return FakeLlm(responses=[["This", " is", " a", " test", " message"]], token_counts=[30, 20, 10])


@pytest.mark.django_db()
def test_list_experiments(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:list-experiments"))
    assert response.status_code == 200
    expected_json = {
        "results": [{"name": experiment.name, "experiment_id": experiment.public_id}],
        "next": None,
        "previous": None,
    }
    assert response.json() == expected_json


@pytest.mark.django_db()
def test_only_experiments_from_the_scoped_team_is_returned():
    experiment_team_1 = ExperimentFactory(team=TeamWithUsersFactory())
    experiment_team_2 = ExperimentFactory(team=TeamWithUsersFactory())
    team1 = experiment_team_1.team
    team2 = experiment_team_2.team
    user = team1.members.first()
    client_team_1 = ApiTestClient(user, team1)
    client_team_2 = ApiTestClient(user, team2)

    # Fetch experiments from team 1
    response = client_team_1.get(reverse("api:list-experiments"))
    experiments = response.json()["results"]
    assert len(experiments) == 1
    assert experiments[0]["experiment_id"] == experiment_team_1.public_id

    # Fetch experiments from team 2
    response = client_team_2.get(reverse("api:list-experiments"))
    experiments = response.json()["results"]
    assert len(experiments) == 1
    assert experiments[0]["experiment_id"] == experiment_team_2.public_id


@pytest.mark.django_db()
def test_update_participant_data():
    identifier = "part1"
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    experiment2 = ExperimentFactory(team=experiment.team)
    participant = Participant.objects.create(identifier=identifier, team=experiment.team)
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    # This call should create ParticipantData
    data = {str(experiment.public_id): {"name": "John"}, str(experiment2.public_id): {"name": "Doe"}}
    url = reverse("api:update-participant-data", kwargs={"participant_id": participant.identifier})
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    participant_data_exp_1 = experiment.participant_data.get(participant=participant)
    participant_data_exp_2 = experiment2.participant_data.get(participant=participant)
    assert participant_data_exp_1.data["name"] == "John"
    assert participant_data_exp_2.data["name"] == "Doe"

    # Let's update the data
    data[str(experiment.public_id)]["name"] = "Harry"
    client.post(url, json.dumps(data), content_type="application/json")
    participant_data_exp_1.refresh_from_db()
    assert participant_data_exp_1.data["name"] == "Harry"


@pytest.mark.django_db()
def test_update_participant_data_returns_404():
    """A 404 will be returned when any experiment in the payload is not found. No updates should occur"""
    identifier = "part1"
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    experiment2 = ExperimentFactory(team=TeamWithUsersFactory())
    participant = Participant.objects.create(identifier=identifier, team=experiment.team)
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    # This call should create ParticipantData for team 1's experiment only
    data = {str(experiment.public_id): {"name": "John"}, str(experiment2.public_id): {"name": "Doe"}}
    url = reverse("api:update-participant-data", kwargs={"participant_id": participant.identifier})
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 404
    assert response.json() == {"errors": [{"message": f"Experiment {experiment2.public_id} not found"}]}
    # Assert that nothing was created
    assert experiment.participant_data.filter(participant=participant).exists() is False


@patch("apps.experiments.models.Experiment.get_llm_service", return_value=FakeLlmService(llm=fake_llm()))
class TestCreateCustomSession:
    def setup(self):
        self.experiment = ExperimentFactory(team=TeamWithUsersFactory())
        self.user = self.experiment.team.members.first()
        self.client = ApiTestClient(self.user, self.experiment.team)

    @pytest.mark.django_db()
    def test_create_new_session(self, get_llm_service_mock):
        data = {
            "ephemeral": False,
            "user_input": "Are you alive?",
            "history": [{"type": "human", "message": "Hi there"}, {"type": "ai", "message": "Hi there human"}],
        }
        url = reverse("api:new-session", kwargs={"experiment_id": self.experiment.public_id})

        response = self.client.post(url, json.dumps(data), content_type="application/json")
        assert response.status_code == 200
        participant = Participant.objects.get(team=self.experiment.team, identifier=self.user.email)
        session = ExperimentSession.objects.get(participant=participant, experiment=self.experiment)
        assert response.json() == {"session_id": str(session.external_id), "response": "This is a test message"}
        assert session.chat.messages.count() == 4

    @pytest.mark.django_db()
    def test_create_new_ephemeral_session(self, get_llm_service_mock):
        data = {
            "ephemeral": True,
            "user_input": "Hi there",
            "history": [{"type": "human", "message": "Hi there"}, {"type": "ai", "message": "Hi there human"}],
        }
        url = reverse("api:new-session", kwargs={"experiment_id": self.experiment.public_id})

        response = self.client.post(url, json.dumps(data), content_type="application/json")
        assert response.status_code == 200
        participant = Participant.objects.get(team=self.experiment.team, identifier=self.user.email)
        assert ExperimentSession.objects.filter(participant=participant, experiment=self.experiment).exists() is False
        assert response.json() == {"session_id": None, "response": "This is a test message"}

    @pytest.mark.django_db()
    def test_create_custom_session_returns_422(self, get_llm_service_mock):
        data = {"ephemeral": False, "user_input": "Hi", "history": [{"type": "sheep", "message": "bah"}]}
        url = reverse("api:new-session", kwargs={"experiment_id": self.experiment.public_id})
        response = self.client.post(url, json.dumps(data), content_type="application/json")
        assert response.status_code == 422
        assert response.json() == {"error": "Unknown message type 'sheep'"}
