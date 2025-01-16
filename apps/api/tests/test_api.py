import base64
import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

import httpx
import pytest
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.api.views import VERIFY_CONNECT_ID_URL
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Participant, ParticipantData
from apps.teams.backends import EXPERIMENT_ADMIN_GROUP, add_user_to_team
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
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
                "version_number": 1,
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
        "version_number": 1,
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


def _setup_channel_participant(experiment, identifier, channel_platform, system_metadata=None):
    participant, _ = Participant.objects.get_or_create(
        team=experiment.team, identifier=identifier, platform=channel_platform
    )
    ParticipantData.objects.create(
        team=experiment.team, participant=participant, content_object=experiment, system_metadata=system_metadata or {}
    )


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@patch("apps.api.tasks.CommCareConnectClient")
def test_update_participant_data_and_setup_connect_channels(connect_client_mock):
    """
    Test that a connect channel is created for a participant where
    1. there isn't one set up already
    2. the experiment has a connect messaging channel linked
    """
    client_instance_mock = connect_client_mock.return_value
    client_instance_mock.create_channel.return_value = "channel_id_2"

    team = TeamWithUsersFactory()
    experiment1 = ExperimentFactory(team=team)
    ExperimentChannelFactory(team=team, experiment=experiment1, platform=ChannelPlatform.TELEGRAM)
    ExperimentChannelFactory(team=team, experiment=experiment1, platform=ChannelPlatform.COMMCARE_CONNECT)
    experiment2 = ExperimentFactory(team=team)
    experiment3 = ExperimentFactory(team=team)

    # Participant 1. This is a telegram participant, so should be ignored
    _setup_channel_participant(experiment1, identifier="97878997", channel_platform=ChannelPlatform.TELEGRAM)

    # Participant 2. Already has a commcare_connect_channel_id, so should be ignored
    _setup_channel_participant(
        experiment1,
        identifier="connectid_1",
        channel_platform=ChannelPlatform.COMMCARE_CONNECT,
        system_metadata={"commcare_connect_channel_id": "f8f5dc93-7d6a-4e9c"},
    )

    # Participant 3.
    # Experiment 1: Doesn't have a connect channel set up
    # Experiment 2: This bot isn't linked to a connect messaging channel
    # Experiment 3: The participant already have a connect channel set up
    # Expectation: Only 1 channel needs to be set up for this participant
    _setup_channel_participant(
        experiment1, identifier="connectid_2", channel_platform=ChannelPlatform.COMMCARE_CONNECT, system_metadata={}
    )

    _setup_channel_participant(
        experiment3,
        identifier="connectid_2",
        channel_platform=ChannelPlatform.COMMCARE_CONNECT,
        system_metadata={"commcare_connect_channel_id": "7d6a-fdc93-4e9c"},
    )

    user = team.members.first()
    client = ApiTestClient(user, team)

    data = {
        "identifier": "connectid_2",
        "platform": "commcare_connect",
        "data": [
            {
                "experiment": str(experiment1.public_id),
                "data": {},
                "schedules": [],
            },
            {
                "experiment": str(experiment2.public_id),
                "data": {},
                "schedules": [],
            },
        ],
    }
    url = reverse("api:update-participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    # Only one of the two experiments that the "connectid_2" participant belongs to has a connect messaging channel, so
    # we expect only one call to the Connect servers to have been made
    assert client_instance_mock.create_channel.call_count == 1
    call_kwargs = client_instance_mock.create_channel.call_args_list[0][1]
    assert call_kwargs["connect_id"] == "connectid_2"
    assert call_kwargs["channel_source"] == f"{experiment1.team}-{experiment1.name}"
    assert Participant.objects.filter(identifier="connectid_2").exists()
    data = ParticipantData.objects.get(participant__identifier="connectid_2", object_id=experiment1.id)
    assert data.system_metadata["commcare_connect_channel_id"] == "channel_id_2"


@pytest.mark.django_db()
class TestConnectApis:
    def _setup_participant(self, experiment, connect_id, channel_id):
        participant = ParticipantFactory(team=experiment.team, identifier=connect_id)
        return ParticipantData.objects.create(
            team=experiment.team,
            participant=participant,
            content_object=experiment,
            system_metadata={"commcare_connect_channel_id": channel_id},
        )

    def _make_key_request(self, client, data):
        token = uuid.uuid4()
        url = reverse("api:commcare-connect:generate_key")
        return client.post(
            url, data=data, headers={"Authorization": f"Bearer {token}"}, content_type="application/json"
        )

    def test_generate_key_success(self, client, experiment, httpx_mock):
        connect_id = uuid.uuid4().hex
        commcare_connect_channel_id = uuid.uuid4().hex
        participant_data = self._setup_participant(
            experiment, connect_id=connect_id, channel_id=commcare_connect_channel_id
        )

        httpx_mock.add_response(method="GET", url=VERIFY_CONNECT_ID_URL, json={"sub": connect_id})
        response = self._make_request(client=client, data={"channel_id": commcare_connect_channel_id})

        assert response.status_code == 200
        base64_key = response.json()["key"]
        assert base64.b64decode(base64_key) is not None
        participant_data.refresh_from_db()
        assert participant_data.encryption_key == base64_key

    def test_generate_key_cannot_the_find_user(self, client, experiment, httpx_mock):
        connect_id = uuid.uuid4().hex
        commcare_connect_channel_id = uuid.uuid4().hex
        self._setup_participant(experiment, connect_id=connect_id, channel_id=commcare_connect_channel_id)

        httpx_mock.add_response(method="GET", url=VERIFY_CONNECT_ID_URL, json={"sub": "garbage"})

        response = self._make_request(client=client, data={"channel_id": commcare_connect_channel_id})
        assert response.status_code == 404

    def test_generate_key_fails_auth_at_connect(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", url=VERIFY_CONNECT_ID_URL, status_code=401)

        with pytest.raises(httpx.HTTPStatusError):
            self._make_request(client=client, data={})

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123123")
    def test_user_consented(self, client, experiment):
        connect_id = uuid.uuid4().hex
        commcare_connect_channel_id = uuid.uuid4().hex
        participant_data = self._setup_participant(
            experiment, connect_id=connect_id, channel_id=commcare_connect_channel_id
        )

        payload = {"channel_id": commcare_connect_channel_id, "consent": True}
        response = client.post(
            reverse("api:commcare-connect:consent"),
            json.dumps(payload),
            headers=self._get_request_headers(payload),
            content_type="application/json",
        )
        assert response.status_code == 200
        participant_data.refresh_from_db()
        assert participant_data.system_metadata == {
            "commcare_connect_channel_id": commcare_connect_channel_id,
            "consent": True,
        }

    def _get_request_headers(self, payload: dict) -> dict:
        msg = json.dumps(payload).encode("utf-8")
        key = settings.COMMCARE_CONNECT_SERVER_SECRET.encode()
        digest = hmac.new(key=key, msg=msg, digestmod=hashlib.sha256).digest()
        return {
            "X-MAC-DIGEST": base64.b64encode(digest),
        }

    def test_invalid_hmac_signature(self, client):
        """Test that requests with invalid HMAC signatures are rejected."""
        payload = {"channel_id": "valid_id", "consent": True}
        headers = {"X-MAC-DIGEST": "invalid_digest"}
        response = client.post(
            reverse("api:commcare-connect:consent"),
            json.dumps(payload),
            headers=headers,
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_empty_request_body(self, client):
        """Test that empty request bodies are rejected."""
        headers = self._get_request_headers({})
        response = client.post(
            reverse("api:commcare-connect:consent"),
            "",
            headers=headers,
            content_type="application/json",
        )
        assert response.status_code == 401
