"""Tests for the participant read API endpoint (GET /api/participants)."""

import json

import pytest
from django.urls import reverse

from apps.events.models import ScheduledMessage
from apps.experiments.models import Participant, ParticipantData
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
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
    response = client.get(reverse("api:participant-data"))
    assert response.status_code == 200
    assert response.json()["count"] == 1
    results = response.json()["results"]
    assert len(results) == 1
    p = results[0]
    assert p["identifier"] == "user1"
    assert p["platform"] == "api"
    assert len(p["data"]) == 2
    experiment_ids = {str(d["chatbot_id"]) for d in p["data"]}
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
    url = reverse("api:participant-data") + "?identifier=alice"
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
    url = reverse("api:participant-data") + "?platform=telegram"
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
    response = client.get(reverse("api:participant-data"))
    assert response.status_code == 200
    results = response.json()["results"]
    identifiers = [p["identifier"] for p in results]
    assert "t1user" in identifiers
    assert "t2user" not in identifiers


# The endpoint accepts both the documented `chatbot` param and the deprecated `experiment`
# alias; the same filtering behaviour must hold for either name.
FILTER_PARAM_CASES = [
    pytest.param("chatbot", id="documented_chatbot_param"),
    pytest.param("experiment", id="deprecated_experiment_alias"),
]


@pytest.mark.django_db()
@pytest.mark.parametrize("filter_param", FILTER_PARAM_CASES)
def test_list_participants_filter_by_chatbot(filter_param):
    team = TeamWithUsersFactory.create()
    experiment1 = ExperimentFactory.create(team=team)
    experiment2 = ExperimentFactory.create(team=team)

    p1 = ParticipantFactory.create(team=team, identifier="alice", platform="api")
    p2 = ParticipantFactory.create(team=team, identifier="bob", platform="api")
    p3 = ParticipantFactory.create(team=team, identifier="carol", platform="api")
    ParticipantData.objects.create(team=team, participant=p1, experiment=experiment1, data={"x": 1})
    ParticipantData.objects.create(team=team, participant=p2, experiment=experiment2, data={"x": 2})
    ParticipantData.objects.create(team=team, participant=p3, experiment=experiment1, data={"x": 3})
    ParticipantData.objects.create(team=team, participant=p3, experiment=experiment2, data={"x": 4})

    user = team.members.first()
    client = ApiTestClient(user, team)
    url = reverse("api:participant-data") + f"?{filter_param}={experiment1.public_id}"
    response = client.get(url)
    assert response.status_code == 200
    results = response.json()["results"]
    by_identifier = {p["identifier"]: p for p in results}
    assert set(by_identifier) == {"alice", "carol"}
    # alice only has data for experiment1
    assert len(by_identifier["alice"]["data"]) == 1
    assert by_identifier["alice"]["data"][0]["chatbot_id"] == str(experiment1.public_id)
    assert by_identifier["alice"]["data"][0]["data"] == {"x": 1}
    # carol has data for both chatbots but only experiment1's data should be returned
    assert len(by_identifier["carol"]["data"]) == 1
    assert by_identifier["carol"]["data"][0]["chatbot_id"] == str(experiment1.public_id)
    assert by_identifier["carol"]["data"][0]["data"] == {"x": 3}


@pytest.mark.django_db()
def test_list_participants_chatbot_takes_precedence_over_experiment_alias():
    """When both are supplied, the documented `chatbot` param wins over the legacy `experiment` alias."""
    team = TeamWithUsersFactory.create()
    experiment1 = ExperimentFactory.create(team=team)
    experiment2 = ExperimentFactory.create(team=team)

    p1 = ParticipantFactory.create(team=team, identifier="alice", platform="api")
    p2 = ParticipantFactory.create(team=team, identifier="bob", platform="api")
    ParticipantData.objects.create(team=team, participant=p1, experiment=experiment1, data={"x": 1})
    ParticipantData.objects.create(team=team, participant=p2, experiment=experiment2, data={"x": 2})

    user = team.members.first()
    client = ApiTestClient(user, team)
    url = reverse("api:participant-data") + f"?chatbot={experiment1.public_id}&experiment={experiment2.public_id}"
    response = client.get(url)
    assert response.status_code == 200
    results = response.json()["results"]
    assert {p["identifier"] for p in results} == {"alice"}


@pytest.mark.django_db()
@pytest.mark.parametrize("filter_param", FILTER_PARAM_CASES)
def test_list_participants_filter_unknown_returns_empty(filter_param):
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    p1 = ParticipantFactory.create(team=team, identifier="alice", platform="api")
    ParticipantData.objects.create(team=team, participant=p1, experiment=experiment, data={"x": 1})

    user = team.members.first()
    client = ApiTestClient(user, team)
    # nil UUID, no chatbot owns this id
    url = reverse("api:participant-data") + f"?{filter_param}=00000000-0000-0000-0000-000000000000"
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["results"] == []


@pytest.mark.django_db()
@pytest.mark.parametrize("filter_param", FILTER_PARAM_CASES)
def test_list_participants_filter_invalid_uuid(filter_param):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)
    url = reverse("api:participant-data") + f"?{filter_param}=not-a-uuid"
    response = client.get(url)
    assert response.status_code == 400
    # the validation error is reported under the param the caller actually used
    assert filter_param in response.json()


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
    url = reverse("api:participant-data")
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 403


@pytest.mark.django_db()
def test_read_only_key_can_get_participants():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team, read_only=True)
    response = client.get(reverse("api:participant-data"))
    assert response.status_code == 200


def _client_for_role(team, group_name, **kwargs):
    """An API client for a new team member holding only `group_name`."""
    user = UserFactory.create()
    add_user_to_team(team, user, [group_name])
    return ApiTestClient(user, team, **kwargs)


# Team membership alone must not grant participant access: the caller's role has to hold the same
# participant model permissions the participant UI requires. Chat Viewer holds none of them,
# Chatbot Admin holds them all.
ROLE_CASES = [
    pytest.param(CHAT_VIEWER_GROUP, 403, id="chat_viewer_denied"),
    pytest.param(CHATBOT_ADMIN_GROUP, 200, id="chatbot_admin_allowed"),
]


@pytest.mark.django_db()
@pytest.mark.parametrize(("group_name", "expected_status"), ROLE_CASES)
def test_list_participants_requires_view_permissions(group_name, expected_status):
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)
    participant = ParticipantFactory.create(team=team, identifier="user1", platform="api")
    ParticipantData.objects.create(team=team, participant=participant, experiment=experiment, data={"secret": "value"})

    client = _client_for_role(team, group_name)
    response = client.get(reverse("api:participant-data"))

    assert response.status_code == expected_status
    if expected_status == 200:
        assert [p["identifier"] for p in response.json()["results"]] == ["user1"]


@pytest.mark.django_db()
@pytest.mark.parametrize(("group_name", "expected_status"), ROLE_CASES)
def test_update_participant_data_requires_write_permissions(group_name, expected_status):
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)
    client = _client_for_role(team, group_name)
    data = {
        "identifier": "part1",
        "platform": "api",
        "data": [{"experiment": str(experiment.public_id), "data": {"name": "John"}}],
    }

    response = client.post(reverse("api:participant-data"), json.dumps(data), content_type="application/json")

    assert response.status_code == expected_status
    assert Participant.objects.filter(team=team, identifier="part1").exists() == (expected_status == 200)


@pytest.mark.django_db()
def test_chatbot_admin_can_create_and_cancel_schedules():
    """Managing participant schedules needs no events permissions, which Chatbot Admin lacks."""
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)
    client = _client_for_role(team, CHATBOT_ADMIN_GROUP)
    url = reverse("api:participant-data")

    create = {
        "identifier": "part1",
        "platform": "api",
        "data": [
            {
                "experiment": str(experiment.public_id),
                "schedules": [
                    {
                        "id": "sched1",
                        "name": "schedule1",
                        "prompt": "tell ET to phone home",
                        "date": "2099-01-01T00:00:00Z",
                    }
                ],
            }
        ],
    }
    response = client.post(url, json.dumps(create), content_type="application/json")
    assert response.status_code == 200, response.content
    schedule = ScheduledMessage.objects.get(external_id="sched1", experiment=experiment)
    assert schedule.custom_schedule_params["prompt_text"] == "tell ET to phone home"
    assert schedule.cancelled_at is None

    cancel = {
        "identifier": "part1",
        "platform": "api",
        "data": [{"experiment": str(experiment.public_id), "schedules": [{"id": "sched1", "delete": True}]}],
    }
    response = client.post(url, json.dumps(cancel), content_type="application/json")
    assert response.status_code == 200, response.content
    schedule.refresh_from_db()
    assert schedule.cancelled_at is not None


@pytest.mark.django_db()
def test_machine_token_reads_and_writes_participants():
    """Machine tokens have no user and so no model permissions; the OAuth scope authorizes them."""
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)
    # The app owner is deliberately a non-member of the pinned team.
    client = ApiTestClient(
        UserFactory.create(),
        team,
        auth_method="oauth_client_credentials",
        scopes=["participants:read", "participants:write"],
    )
    url = reverse("api:participant-data")

    data = {
        "identifier": "part1",
        "platform": "api",
        "data": [
            {
                "experiment": str(experiment.public_id),
                "data": {"name": "John"},
                "schedules": [
                    {"id": "sched1", "name": "schedule1", "prompt": "phone home", "date": "2099-01-01T00:00:00Z"}
                ],
            }
        ],
    }
    response = client.post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 200, response.content
    assert ScheduledMessage.objects.filter(external_id="sched1", experiment=experiment).exists()

    response = client.get(url)
    assert response.status_code == 200, response.content
    assert [p["identifier"] for p in response.json()["results"]] == ["part1"]
