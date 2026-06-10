import base64
import hashlib
import hmac
import json
import os
import uuid
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession, Participant, ParticipantData, SessionStatus
from apps.teams.backends import CHATBOT_ADMIN_GROUP, add_user_to_team
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient
from apps.utils.tests.langchain import mock_llm


@pytest.fixture()
def experiment(db):
    exp = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    LlmProviderFactory.create(team=exp.team)
    return exp


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_experiments(auth_method, experiment):
    user = experiment.team.members.first()
    version1 = experiment.create_new_version()
    version2 = experiment.create_new_version()
    client = ApiTestClient(user, experiment.team, auth_method=auth_method)
    response = client.get(reverse("api:experiment-list"))
    assert response.status_code == 200
    expected_json = {
        "results": [
            {
                "name": experiment.name,
                "id": experiment.public_id,
                "url": f"http://testserver/api/experiments/{experiment.public_id}/",
                "version_number": 3,
                "versions": [
                    {"name": version1.name, "version_number": 1, "is_default_version": True, "version_description": ""},
                    {
                        "name": version2.name,
                        "version_number": 2,
                        "is_default_version": False,
                        "version_description": "",
                    },
                ],
            }
        ],
        "next": None,
        "previous": None,
        "count": 1,
    }
    assert response.json() == expected_json


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_retrieve_experiments(auth_method, experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team, auth_method=auth_method)
    response = client.get(reverse("api:experiment-detail", kwargs={"id": experiment.public_id}))
    assert response.status_code == 200
    assert response.json() == {
        "id": experiment.public_id,
        "name": experiment.name,
        "url": f"http://testserver/api/experiments/{experiment.public_id}/",
        "version_number": 1,
        "versions": [],
    }


@pytest.mark.django_db()
def test_only_experiments_from_the_scoped_team_is_returned():
    experiment_team_1 = ExperimentFactory.create(team=TeamWithUsersFactory.create(member__user__username="uname1"))
    experiment_team_2 = ExperimentFactory.create(team=TeamWithUsersFactory.create(member__user__username="uname2"))
    team1 = experiment_team_1.team
    team2 = experiment_team_2.team

    user = team1.members.first()
    add_user_to_team(team2, user, [CHATBOT_ADMIN_GROUP])

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
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_create_and_update_participant_data(auth_method):
    identifier = "part1"
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    experiment2 = ExperimentFactory.create(team=experiment.team)
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team, auth_method=auth_method)

    # This call should create ParticipantData
    data = {
        "identifier": identifier,
        "platform": "api",
        "data": [
            {"experiment": str(experiment.public_id), "data": {"name": "John"}},
            {"experiment": str(experiment2.public_id), "data": {"name": "Doe"}},
        ],
    }
    url = reverse("api:participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["identifier"] == identifier
    assert response_json["platform"] == "api"
    entries = {entry["chatbot_id"]: entry for entry in response_json["data"]}
    assert entries[str(experiment.public_id)]["data"] == {"name": "John"}
    assert entries[str(experiment.public_id)]["connect_channel_id"] is None
    assert entries[str(experiment2.public_id)]["data"] == {"name": "Doe"}

    participant = Participant.objects.get(identifier=identifier)
    assert participant.name == ""
    participant_data_exp_1 = experiment.participantdata_set.get(participant__identifier=identifier)
    participant_data_exp_2 = experiment2.participantdata_set.get(participant__identifier=identifier)
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
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    experiment2 = ExperimentFactory.create(team=TeamWithUsersFactory.create())
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
    url = reverse("api:participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 404
    assert response.json() == {"errors": [{"message": f"Experiment {experiment2.public_id} not found"}]}
    # Assert that nothing was created
    assert experiment.participantdata_set.filter(participant=participant).exists() is False


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
    url = reverse("api:participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    participant_data_exp_1 = experiment.participantdata_set.get(participant__identifier=identifier)
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
    url = reverse("api:participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    # make sure the data hasn't changed
    participant_data = experiment.participantdata_set.get(participant__identifier=identifier)
    assert participant_data.data["name"] == "John"

    updated_schedules = list(
        experiment.scheduled_messages.filter(participant__identifier=identifier).order_by("next_trigger_date")
    )
    assert len(updated_schedules) == 3
    assert updated_schedules[0].cancelled_at is not None
    assert updated_schedules[1].custom_schedule_params == {
        "name": "schedule2 updated",
        "prompt_text": "email john smith",
        "repetitions": 1,
        "frequency": 1,
        "time_period": "days",
    }
    assert updated_schedules[1].next_trigger_date == schedules[1].next_trigger_date

    assert updated_schedules[2].custom_schedule_params == {
        "name": "schedule3",
        "prompt_text": "don't forget to floss",
        "repetitions": 1,
        "frequency": 1,
        "time_period": "days",
    }
    assert updated_schedules[2].next_trigger_date == trigger_date3


@pytest.mark.django_db()
def test_list_participants_includes_connect_channel_id(experiment):
    participant = ParticipantFactory(team=experiment.team, platform="commcare_connect")
    ParticipantData.objects.create(
        team=experiment.team,
        participant=participant,
        experiment=experiment,
        system_metadata={"commcare_connect_channel_id": "abc-123", "consent": True},
    )
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)

    response = client.get(reverse("api:participant-data"))

    assert response.status_code == 200
    results = response.json()["results"]
    entry = next(p for p in results if p["identifier"] == participant.identifier)
    assert entry["data"][0]["connect_channel_id"] == "abc-123"


def _setup_channel_participant(experiment, identifier, channel_platform, system_metadata=None):
    participant, _ = Participant.objects.get_or_create(
        team=experiment.team, identifier=identifier, platform=channel_platform
    )
    ParticipantData.objects.create(
        team=experiment.team, participant=participant, experiment=experiment, system_metadata=system_metadata or {}
    )


@pytest.mark.django_db()
@override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
def test_update_participant_data_and_setup_connect_channels(httpx_mock):
    """
    Test that a connect channel is created for a participant where
    1. there isn't one set up already
    2. the experiment has a connect messaging channel linked
    """
    created_connect_channel_id = str(uuid.uuid4())
    httpx_mock.add_response(
        method="POST",
        url=f"{settings.COMMCARE_CONNECT_SERVER_URL}/messaging/create_channel/",
        json={"channel_id": created_connect_channel_id, "consent": True},
    )

    team = TeamWithUsersFactory.create()
    experiment1 = ExperimentFactory.create(team=team)
    ExperimentChannelFactory.create(team=team, experiment=experiment1, platform=ChannelPlatform.TELEGRAM)
    ExperimentChannelFactory.create(
        team=team,
        experiment=experiment1,
        platform=ChannelPlatform.COMMCARE_CONNECT,
        extra_data={"commcare_connect_bot_name": "bot1"},
    )
    experiment2 = ExperimentFactory.create(team=team)
    experiment3 = ExperimentFactory.create(team=team)

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
        "identifier": "ConnectID_2",
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
    url = reverse("api:participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    response_json = response.json()
    assert response_json["identifier"] == "connectid_2"
    channel_ids = {entry["chatbot_id"]: entry["connect_channel_id"] for entry in response_json["data"]}
    assert channel_ids == {
        str(experiment1.public_id): created_connect_channel_id,
        str(experiment2.public_id): None,
        str(experiment3.public_id): "7d6a-fdc93-4e9c",
    }

    # Only one of the two experiments that the "ConnectID_2" participant belongs to has a connect messaging channel, so
    # we expect only one call to the Connect servers to have been made
    request = httpx_mock.get_request()
    request_data = json.loads(request.read())
    assert request_data["connectid"] == "connectid_2"
    assert request_data["channel_source"] == "bot1"
    assert Participant.objects.filter(identifier="connectid_2").exists()
    data = ParticipantData.objects.get(participant__identifier="connectid_2", experiment_id=experiment1.id)
    assert data.system_metadata == {"commcare_connect_channel_id": created_connect_channel_id, "consent": True}


@pytest.mark.django_db()
@override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
@pytest.mark.parametrize(
    ("upstream", "expected_status"),
    [
        (500, 503),
        (404, 404),
        (400, 400),
        ("network_error", 503),
    ],
)
def test_update_participant_data_connect_channel_failure(httpx_mock, upstream, expected_status):
    """A Connect API failure fails the request, but the participant data remains saved."""
    url = f"{settings.COMMCARE_CONNECT_SERVER_URL}/messaging/create_channel/"
    if upstream == "network_error":
        # the client retries network errors up to 3 times
        for _ in range(3):
            httpx_mock.add_exception(httpx.ConnectError("connection failed"), method="POST", url=url)
    else:
        httpx_mock.add_response(method="POST", url=url, status_code=upstream)

    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    ExperimentChannelFactory.create(
        team=team,
        experiment=experiment,
        platform=ChannelPlatform.COMMCARE_CONNECT,
        extra_data={"commcare_connect_bot_name": "bot1"},
    )
    user = team.members.first()
    client = ApiTestClient(user, team)

    data = {
        "identifier": "connectid_3",
        "platform": "commcare_connect",
        "data": [{"experiment": str(experiment.public_id), "data": {"name": "John"}}],
    }
    response = client.post(reverse("api:participant-data"), json.dumps(data), content_type="application/json")

    assert response.status_code == expected_status
    assert "Failed to create channel" in response.json()["detail"]
    # the participant data was saved despite the channel-creation failure
    participant_data = ParticipantData.objects.get(participant__identifier="connectid_3", experiment=experiment)
    assert participant_data.data == {"name": "John"}
    assert "commcare_connect_channel_id" not in participant_data.system_metadata


@pytest.mark.django_db()
def test_register_connect_participant(client, experiment):
    """
    Test registration of a participant with a connect ID. We want to ensure that if a participant already exists with
    the same connect ID (case insensitive), we don't create a duplicate participant.
    """
    connect_id = "connectid_1"
    team = experiment.team
    # Setup participant with lowercase connect ID
    _setup_channel_participant(
        experiment,
        identifier=connect_id.lower(),
        channel_platform=ChannelPlatform.COMMCARE_CONNECT,
    )
    assert Participant.objects.filter(identifier=connect_id.lower()).exists() is True

    user = team.members.first()
    client = ApiTestClient(user, team)

    data = {
        "identifier": connect_id.upper(),
        "platform": "commcare_connect",
        "data": [
            {
                "experiment": str(experiment.public_id),
                "data": {},
                "schedules": [],
            },
        ],
    }
    url = reverse("api:participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    assert Participant.objects.filter(identifier=connect_id.lower()).exists() is True
    assert Participant.objects.filter(identifier=connect_id.upper()).exists() is False


@pytest.mark.django_db()
class TestConnectApis:
    def _make_key_request(self, client, data):
        token = uuid.uuid4()
        url = reverse("api:commcare-connect:generate_key")
        return client.post(url, data=data, headers={"Authorization": f"Bearer {token}"})

    def test_generate_key_success(self, client, experiment, httpx_mock):
        connect_id = uuid.uuid4().hex
        commcare_connect_channel_id = uuid.uuid4().hex
        participant_data = _setup_participant_data(
            experiment,
            connect_id=connect_id,
            system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id},
        )

        httpx_mock.add_response(
            method="GET", url=settings.COMMCARE_CONNECT_GET_CONNECT_ID_URL, json={"sub": connect_id}
        )
        response = self._make_key_request(client=client, data={"channel_id": commcare_connect_channel_id})

        assert response.status_code == 200
        base64_key = response.json()["key"]
        assert base64.b64decode(base64_key) is not None
        participant_data.refresh_from_db()
        assert participant_data.encryption_key == base64_key

    def test_generate_key_cannot_the_find_user(self, client, experiment, httpx_mock):
        connect_id = uuid.uuid4().hex
        commcare_connect_channel_id = uuid.uuid4().hex
        _setup_participant_data(
            experiment,
            connect_id=connect_id,
            system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id},
        )

        httpx_mock.add_response(method="GET", url=settings.COMMCARE_CONNECT_GET_CONNECT_ID_URL, json={"sub": "garbage"})

        response = self._make_key_request(client=client, data={"channel_id": commcare_connect_channel_id})
        assert response.status_code == 404

    def test_generate_key_fails_auth_at_connect(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", url=settings.COMMCARE_CONNECT_GET_CONNECT_ID_URL, status_code=401)

        with pytest.raises(httpx.HTTPStatusError):
            self._make_key_request(client=client, data={"channel_id": "123"})

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123123")
    def test_user_consented(self, client, experiment):
        connect_id = uuid.uuid4().hex
        commcare_connect_channel_id = uuid.uuid4().hex
        participant_data = _setup_participant_data(
            experiment,
            connect_id=connect_id,
            system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id},
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


def _setup_participant_data(
    experiment,
    connect_id,
    system_metadata: dict,
    encryption_key=None,
):
    participant = ParticipantFactory.create(
        team=experiment.team, identifier=connect_id, platform=ChannelPlatform.COMMCARE_CONNECT
    )
    return ParticipantData.objects.create(
        team=experiment.team,
        participant=participant,
        experiment=experiment,
        system_metadata=system_metadata,
        encryption_key=encryption_key or "",
    )


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient")
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_generate_bot_message_and_send(ConnectClient, experiment, auth_method, django_capture_on_commit_callbacks):
    """
    Test that a bot message is generated and sent to a participant. If there isn't a session for the participant yet,
    we expect one to be created. The generated bot message should be saved as an AI message, but the prompt should not
    be saved.
    """
    connect_client_mock = ConnectClient.return_value

    connect_id = uuid.uuid4().hex
    commcare_connect_channel_id = uuid.uuid4().hex
    encryption_key = os.urandom(32).hex()
    participant_data = _setup_participant_data(
        experiment,
        connect_id=connect_id,
        system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id, "consent": True},
        encryption_key=encryption_key,
    )
    ExperimentChannelFactory.create(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
    )

    assert (
        ExperimentSession.objects.filter(participant=participant_data.participant, experiment=experiment).exists()
        is False
    )

    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team, auth_method=auth_method)

    data: dict[str, Any] = {
        "identifier": connect_id,
        "platform": ChannelPlatform.COMMCARE_CONNECT,
        "experiment": str(experiment.public_id),
        "prompt_text": "Tell the user to take a break and make a beverege",
    }
    url = reverse("api:trigger_bot")
    with mock_llm(["Time to take a break and brew some coffee"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200
    connect_client_mock.send_message_to_user.assert_called()
    kwargs = connect_client_mock.send_message_to_user.call_args.kwargs
    assert kwargs["message"] == "Time to take a break and brew some coffee"
    session = ExperimentSession.objects.get(participant=participant_data.participant, experiment=experiment)
    assert session.chat.messages.count() == 1
    first_message = session.chat.messages.first()
    assert first_message.message_type == "ai"
    assert first_message.content == "Time to take a break and brew some coffee"
    response_data = response.json()
    assert response_data["session_id"] == str(session.external_id)
    assert response_data["channel"] == ChannelPlatform.COMMCARE_CONNECT
    assert response_data["team"] == {"name": experiment.team.name, "slug": experiment.team.slug}
    assert f"/api/sessions/{session.external_id}/" in response_data["url"]

    # Call it a second time to make sure the session is reused
    with mock_llm(["Time to take a break and brew some tea"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200
    assert response.json()["session_id"] == str(session.external_id)  # same session reused
    session = ExperimentSession.objects.get(participant=participant_data.participant, experiment=experiment)
    assert session.chat.messages.count() == 2
    last_message = session.chat.messages.last()
    assert last_message.message_type == "ai"
    assert last_message.content == "Time to take a break and brew some tea"

    # Call it a third time, but this time we want to start a new session
    first_session = session
    data["start_new_session"] = True
    with mock_llm(["Time to take a break an juice some fruit"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200
    first_session.refresh_from_db()
    assert first_session.status == SessionStatus.PENDING_REVIEW
    new_session = (
        ExperimentSession.objects.filter(participant=participant_data.participant, experiment=experiment)
        .order_by("-created_at")
        .first()
    )
    assert response.json()["session_id"] == str(new_session.external_id)  # new session ID returned
    assert new_session.chat.messages.count() == 1
    last_message = new_session.chat.messages.last()
    assert last_message.message_type == "ai"
    assert last_message.content == "Time to take a break an juice some fruit"


@pytest.mark.django_db()
@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True, COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123"
)
@patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient")
@pytest.mark.parametrize("consented", [True, False])
def test_generate_bot_message_auto_creates_participant(
    ConnectClient, experiment, httpx_mock, consented, django_capture_on_commit_callbacks
):
    """
    Test that trigger_bot_message auto-creates participant and participant_data if they don't exist.
    This supports the auto-consent flow from CommCare Connect.
    """
    connect_client_mock = ConnectClient.return_value

    connect_id = uuid.uuid4().hex
    created_connect_channel_id = str(uuid.uuid4())

    # Setup the experiment with a CCC channel
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.COMMCARE_CONNECT,
        extra_data={"commcare_connect_bot_name": "test_bot"},
    )

    # Mock the CCC API call that creates the channel and returns consent
    httpx_mock.add_response(
        method="POST",
        url=f"{settings.COMMCARE_CONNECT_SERVER_URL}/messaging/create_channel/",
        json={"channel_id": created_connect_channel_id, "consent": consented},
    )

    # Verify participant doesn't exist yet
    assert not Participant.objects.filter(identifier=connect_id, platform=ChannelPlatform.COMMCARE_CONNECT).exists()
    assert not ParticipantData.objects.filter(
        participant__identifier=connect_id,
        experiment=experiment,
    ).exists()

    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team)

    data = {
        "identifier": connect_id,
        "platform": ChannelPlatform.COMMCARE_CONNECT,
        "experiment": str(experiment.public_id),
        "prompt_text": "Welcome to the bot!",
    }
    url = reverse("api:trigger_bot")
    with mock_llm(["Welcome! How can I help you today?"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")

    assert response.status_code == 200 if consented else 400
    if not consented:
        assert response.json()["detail"] == "User has not given consent"

    # Verify participant and participant_data were created despite the error
    participant = Participant.objects.get(identifier=connect_id, platform=ChannelPlatform.COMMCARE_CONNECT)
    assert participant is not None
    assert participant.team == experiment.team

    participant_data = ParticipantData.objects.get(participant=participant, experiment=experiment)
    assert participant_data is not None
    assert participant_data.system_metadata["commcare_connect_channel_id"] == created_connect_channel_id
    assert participant_data.system_metadata["consent"] is consented

    if consented:
        # Verify the message was sent
        connect_client_mock.send_message_to_user.assert_called()
        kwargs = connect_client_mock.send_message_to_user.call_args.kwargs
        assert kwargs["message"] == "Welcome! How can I help you today?"

        # Verify session and message were created
        session = ExperimentSession.objects.get(participant=participant, experiment=experiment)
        assert session.chat.messages.count() == 1
        message = session.chat.messages.first()
        assert message.message_type == "ai"
        assert message.content == "Welcome! How can I help you today?"
    else:
        connect_client_mock.send_message_to_user.assert_not_called()
        assert not ExperimentSession.objects.filter(participant=participant, experiment=experiment).exists()


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_generate_bot_message_for_email_channel(experiment, django_capture_on_commit_callbacks):
    """Regression: trigger_bot_message_task must work for the v2 EmailChannel.

    Previously raised AttributeError because v2 ChannelBase lacked
    ``ensure_session_exists_for_participant``.
    """
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
    )

    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team)

    data = {
        "identifier": "user@example.com",
        "platform": ChannelPlatform.EMAIL,
        "experiment": str(experiment.public_id),
        "prompt_text": "Say hello",
    }
    url = reverse("api:trigger_bot")
    with mock_llm(["Hello from the bot"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")

    assert response.status_code == 200

    # Session was created and tied to the email participant
    participant = Participant.objects.get(identifier="user@example.com", platform=ChannelPlatform.EMAIL)
    session = ExperimentSession.objects.get(participant=participant, experiment=experiment)
    assert session.chat.messages.count() == 1
    bot_message = session.chat.messages.first()
    assert bot_message.message_type == "ai"
    assert bot_message.content == "Hello from the bot"
    response_data = response.json()
    assert response_data["session_id"] == str(session.external_id)
    assert response_data["channel"] == ChannelPlatform.EMAIL

    # Email actually delivered through the EmailSender
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.body == "Hello from the bot"
    assert sent.to == ["user@example.com"]
    assert sent.from_email == "bot@chat.openchatstudio.com"


# ── trigger_bot direct-message (message_text) tests ──────────────────────────


@pytest.mark.django_db()
@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True, COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123"
)
@patch("apps.api.views.channels.CommCareConnectClient")
@patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient")
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_trigger_bot_direct_message(
    ConnectClientChat, ConnectClientView, experiment, auth_method, django_capture_on_commit_callbacks
):
    """
    trigger_bot with message_text delivers the message directly without going through the LLM.
    """
    connect_client_mock = ConnectClientChat.return_value
    connect_id = uuid.uuid4().hex
    commcare_connect_channel_id = uuid.uuid4().hex
    encryption_key = os.urandom(32).hex()
    participant_data = _setup_participant_data(
        experiment,
        connect_id=connect_id,
        system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id, "consent": True},
        encryption_key=encryption_key,
    )
    ExperimentChannelFactory.create(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
    )
    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team, auth_method=auth_method)
    message = "Your appointment is confirmed for tomorrow at 10am."
    data: dict[str, Any] = {
        "identifier": connect_id,
        "platform": ChannelPlatform.COMMCARE_CONNECT,
        "experiment": str(experiment.public_id),
        "message_text": message,
    }
    url = reverse("api:trigger_bot")
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    # Message delivered via channel, not via LLM
    connect_client_mock.send_message_to_user.assert_called()
    kwargs = connect_client_mock.send_message_to_user.call_args.kwargs
    assert kwargs["message"] == message

    # Message recorded in chat history as an AI message
    session = ExperimentSession.objects.get(participant=participant_data.participant, experiment=experiment)
    assert session.chat.messages.count() == 1
    saved_msg = session.chat.messages.first()
    assert saved_msg.message_type == "ai"
    assert saved_msg.content == message
    response_data = response.json()
    assert response_data["session_id"] == str(session.external_id)
    assert response_data["channel"] == ChannelPlatform.COMMCARE_CONNECT


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_trigger_bot_direct_message_for_email_channel(experiment, django_capture_on_commit_callbacks):
    """
    trigger_bot with message_text must work for the EmailChannel (delivers via email, no LLM).
    """
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
    )
    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team)
    message = "Hello from the platform!"
    data = {
        "identifier": "user@example.com",
        "platform": ChannelPlatform.EMAIL,
        "experiment": str(experiment.public_id),
        "message_text": message,
    }
    url = reverse("api:trigger_bot")
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200

    # Session and message created
    participant = Participant.objects.get(identifier="user@example.com", platform=ChannelPlatform.EMAIL)
    session = ExperimentSession.objects.get(participant=participant, experiment=experiment)
    assert session.chat.messages.count() == 1
    saved_msg = session.chat.messages.first()
    assert saved_msg.message_type == "ai"
    assert saved_msg.content == message

    # Email delivered directly (no LLM)
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.body == message
    assert sent.to == ["user@example.com"]


@pytest.mark.django_db()
@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True, COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123"
)
@patch("apps.api.views.channels.CommCareConnectClient")
@patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient")
def test_trigger_bot_direct_message_consent_required(ConnectClientChat, ConnectClientView, experiment, httpx_mock):
    """
    trigger_bot with message_text should return 400 when the participant has not consented (CCC platform).
    """
    connect_id = uuid.uuid4().hex
    commcare_connect_channel_id = uuid.uuid4().hex
    _setup_participant_data(
        experiment,
        connect_id=connect_id,
        system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id, "consent": False},
    )
    ExperimentChannelFactory.create(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
    )
    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team)
    data = {
        "identifier": connect_id,
        "platform": ChannelPlatform.COMMCARE_CONNECT,
        "experiment": str(experiment.public_id),
        "message_text": "This should not be delivered.",
    }
    url = reverse("api:trigger_bot")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 400
    assert response.json()["detail"] == "User has not given consent"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("data_override", "expected_error"),
    [
        ({}, "Either 'prompt_text' or 'message_text' must be provided."),
        (
            {"prompt_text": "do something", "message_text": "direct text"},
            "Provide either 'prompt_text' or 'message_text', not both.",
        ),
    ],
)
def test_trigger_bot_requires_prompt_or_message_text(experiment, data_override, expected_error):
    """trigger_bot must reject requests that supply neither or both text fields."""
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
    )
    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team)
    data = {
        "identifier": "user@example.com",
        "platform": ChannelPlatform.EMAIL,
        "experiment": str(experiment.public_id),
        **data_override,
    }
    url = reverse("api:trigger_bot")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 400
    errors = response.json()
    # DRF wraps non_field_errors under "non_field_errors" key
    assert any(expected_error in str(v) for v in errors.values())
