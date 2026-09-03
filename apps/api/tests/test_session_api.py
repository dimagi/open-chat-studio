from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.fields import DateTimeField

from apps.annotations.models import Tag, TagCategories
from apps.chat.models import ChatAttachment
from apps.experiments.models import ExperimentSession
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantDataFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.factories.traces import TraceFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    return ExperimentFactory.create(team=TeamWithUsersFactory.create())


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment)


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_sessions(auth_method, session):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, auth_method=auth_method)
    response = client.get(reverse("api:session-list"))
    assert response.status_code == 200
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": [get_session_json(session)],
        "count": 1,
    }


@pytest.mark.django_db()
def test_list_sessions_with_tag(experiment):
    team = experiment.team
    user = experiment.team.members.first()
    sessions = ExperimentSessionFactory.create_batch(3, experiment=experiment)

    tags = Tag.objects.bulk_create(
        [
            Tag(name="interesting", slug="interesting", team=team, created_by=user),
            Tag(name="awesome", slug="awesome", team=team, created_by=user),
        ]
    )

    session1 = sessions[0]
    session2 = sessions[1]

    session1.chat.add_tag(tags[0], team, user)
    session2.chat.add_tag(tags[1], team, user)

    client = ApiTestClient(user, team)
    # Filter by tag
    response = client.get(reverse("api:session-list") + "?tags=interesting,awesome")
    assert response.status_code == 200
    expected_results = [
        get_session_json(session2, expected_tags=["awesome"]),
        get_session_json(session1, expected_tags=["interesting"]),
    ]
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": expected_results,
        "count": 2,
    }

    # Remove filters by tag
    response = client.get(reverse("api:session-list"))
    expected_results = [
        get_session_json(sessions[2]),
        get_session_json(session2, expected_tags=["awesome"]),
        get_session_json(session1, expected_tags=["interesting"]),
    ]
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": expected_results,
        "count": 3,
    }


@pytest.mark.django_db()
def test_list_sessions_count_only_on_first_page(experiment):
    """The total count is computed once, on the first page; cursor-following
    requests skip the COUNT query and omit the field."""
    user = experiment.team.members.first()
    ExperimentSessionFactory.create_batch(3, experiment=experiment)

    client = ApiTestClient(user, experiment.team)
    response = client.get(reverse("api:session-list") + "?page_size=2")
    assert response.status_code == 200
    first_page = response.json()
    assert first_page["count"] == 3
    assert len(first_page["results"]) == 2
    assert first_page["next"] is not None

    response = client.get(first_page["next"])
    assert response.status_code == 200
    second_page = response.json()
    assert "count" not in second_page
    assert len(second_page["results"]) == 1


def get_session_json(
    session,
    expected_messages=None,
    expected_tags=None,
    expected_usage=None,
    expected_participant_data=None,
):
    experiment = session.experiment
    data = {
        "url": f"http://testserver/api/sessions/{session.external_id}/",
        "experiment": {
            "id": str(experiment.public_id),
            "name": experiment.name,
            "description": experiment.description,
            "url": f"http://testserver/api/experiments/{experiment.public_id}/",
            "version_number": 1,
        },
        "participant": {"identifier": session.participant.identifier, "remote_id": ""},
        "id": str(session.external_id),
        "team": {
            "name": session.team.name,
            "slug": session.team.slug,
        },
        "created_at": DateTimeField().to_representation(session.created_at),
        "updated_at": DateTimeField().to_representation(session.updated_at),
        "ended_at": DateTimeField().to_representation(session.ended_at) if session.ended_at else None,
        "status": session.status,
        "platform": session.platform,
        "tags": expected_tags if expected_tags is not None else [],
        "state": session.state,
        "participant_data": expected_participant_data if expected_participant_data is not None else {},
    }
    if expected_messages is not None:
        data["messages"] = expected_messages
        data["usage"] = expected_usage if expected_usage is not None else {"total_cost": "0.00000000", "by_model": []}
    return data


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_retrieve_session(auth_method, session):
    user = session.team.members.first()

    tags = Tag.objects.bulk_create(
        [
            Tag(name="tag1", slug="tag1", team=session.team, created_by=user),
            Tag(name="tag2", slug="tag2", team=session.team, created_by=user),
        ]
    )

    session.chat.messages.create(message_type="ai", content="hi")
    message1 = session.chat.messages.create(message_type="human", content="hello")
    files = _create_attachments(session.chat, message1)

    session.chat.add_tag(tags[0], session.team, user)

    message = session.chat.messages.create(message_type="human", content="rabbit in a hat", summary="Abracadabra")
    message.add_tag(tags[0], session.team, user)
    message.add_tag(tags[1], session.team, user)

    client = ApiTestClient(user, session.team, auth_method=auth_method)
    response = client.get(reverse("api:session-detail", kwargs={"id": session.external_id}))
    assert response.status_code == 200
    response_json = response.json()

    for message in response_json.get("messages", []):
        message["created_at"] = "fake date"
        message["attachments"] = sorted(message["attachments"], key=lambda x: x["name"])

    assert response_json == get_session_json(
        session,
        expected_messages=[
            {
                "created_at": "fake date",
                "role": "assistant",
                "content": "hi",
                "metadata": {},
                "tags": [],
                "attachments": [],
            },
            {
                "created_at": "fake date",
                "role": "user",
                "content": "hello",
                "metadata": {},
                "tags": [],
                "attachments": [
                    {
                        "name": "file_1",
                        "content_type": "text/plain",
                        "size": 0,
                        "content_url": f"http://testserver/api/files/{files[0].id}/content",
                    },
                    {
                        "name": "file_2",
                        "content_type": "text/plain",
                        "size": 0,
                        "content_url": f"http://testserver/api/files/{files[1].id}/content",
                    },
                ],
            },
            {
                "created_at": "fake date",
                "role": "system",
                "content": "Abracadabra",
                "metadata": {"compression_marker": "summarize"},
                "tags": [],
                "attachments": [],
            },
            {
                "created_at": "fake date",
                "role": "user",
                "content": "rabbit in a hat",
                "metadata": {},
                "tags": ["tag1", "tag2"],
                "attachments": [],
            },
        ],
        expected_tags=["tag1"],
    )


@pytest.mark.django_db()
def test_retrieve_session_includes_usage_breakdown(session):
    team = session.team
    UsageRecordFactory.create(team=team, session=session, model_name="gpt-4o", cost=Decimal("1.00"), quantity=100)
    UsageRecordFactory.create(team=team, session=session, model_name="gpt-4o", cost=Decimal("2.00"), quantity=200)
    UsageRecordFactory.create(team=team, session=session, model_name="gpt-4o-mini", cost=Decimal("0.50"), quantity=50)
    user = team.members.first()
    response = ApiTestClient(user, team).get(reverse("api:session-detail", kwargs={"id": session.external_id}))
    assert response.status_code == 200
    assert response.json()["usage"] == {
        "total_cost": "3.50000000",
        "by_model": [
            {"model_name": "gpt-4o", "cost": "3.00000000", "tokens": 300},
            {"model_name": "gpt-4o-mini", "cost": "0.50000000", "tokens": 50},
        ],
    }


@pytest.mark.django_db()
def test_session_includes_ended_at(session):
    ended = timezone.now()
    session.ended_at = ended
    session.save()
    user = session.team.members.first()
    response = ApiTestClient(user, session.team).get(reverse("api:session-list"))
    assert response.status_code == 200
    assert response.json()["results"][0]["ended_at"] == DateTimeField().to_representation(ended)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("has_trace", "has_participant_data"),
    [
        pytest.param(False, False, id="no-trace-no-participant-data"),
        pytest.param(False, True, id="no-trace-with-participant-data"),
        pytest.param(True, True, id="with-trace-and-participant-data"),
    ],
)
def test_session_participant_data(session, has_trace, has_participant_data):
    participant_data_value = {"name": "Alice", "language": "en"} if has_participant_data else {}

    if has_participant_data:
        ParticipantDataFactory.create(
            team=session.team,
            experiment=session.experiment,
            participant=session.participant,
            data=participant_data_value,
        )

    if has_trace:
        TraceFactory.create(
            session=session,
            participant=session.participant,
            experiment=session.experiment,
            team=session.team,
            participant_data=participant_data_value,
            participant_data_diff=None,
        )

    user = session.team.members.first()
    client = ApiTestClient(user, session.team)

    for url in [
        reverse("api:session-list"),
        reverse("api:session-detail", kwargs={"id": session.external_id}),
    ]:
        response = client.get(url)
        assert response.status_code == 200
        result = response.json()["results"][0] if "results" in response.json() else response.json()
        assert result["participant_data"] == participant_data_value


@pytest.mark.django_db()
def test_participant_data_prefetch_uses_only_latest_trace_per_session(experiment):
    user = experiment.team.members.first()
    session1, session2 = ExperimentSessionFactory.create_batch(2, experiment=experiment)

    TraceFactory.create(session=session1, team=experiment.team, participant_data={"s": 1})
    TraceFactory.create(session=session1, team=experiment.team, participant_data={"s": "latest"})
    TraceFactory.create(session=session2, team=experiment.team, participant_data={"s": 2})

    response = ApiTestClient(user, experiment.team).get(reverse("api:session-list"))
    assert response.status_code == 200
    by_id = {r["id"]: r for r in response.json()["results"]}
    assert by_id[str(session1.external_id)]["participant_data"] == {"s": "latest"}
    assert by_id[str(session2.external_id)]["participant_data"] == {"s": 2}


def _create_attachments(chat, message):
    tool_resource, _created = ChatAttachment.objects.get_or_create(
        chat_id=chat.id,
        tool_type="file_search",
    )
    file_ids = ["file_1", "file_2"]
    files = []
    for external_id in file_ids:
        files.append(FileFactory.create(name=external_id, external_id=external_id))
    tool_resource.files.add(*files)
    message.metadata = {"openai_file_ids": file_ids}
    message.save()
    return files


@pytest.mark.django_db()
def test_list_sessions_with_experiment_filter(experiment):
    team = experiment.team
    user = experiment.team.members.first()

    # Create another experiment in the same team
    experiment2 = ExperimentFactory.create(team=team)

    # Create sessions for both experiments
    session1 = ExperimentSessionFactory.create(experiment=experiment)
    session2 = ExperimentSessionFactory.create(experiment=experiment2)
    session3 = ExperimentSessionFactory.create(experiment=experiment)

    client = ApiTestClient(user, team)

    # Filter by first experiment
    response = client.get(reverse("api:session-list") + f"?experiment={experiment.public_id}")
    assert response.status_code == 200
    expected_results = [
        get_session_json(session3),
        get_session_json(session1),
    ]
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": expected_results,
        "count": 2,
    }

    # Filter by second experiment
    response = client.get(reverse("api:session-list") + f"?experiment={experiment2.public_id}")
    assert response.status_code == 200
    expected_results = [
        get_session_json(session2),
    ]
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": expected_results,
        "count": 1,
    }


@pytest.mark.django_db()
def test_list_sessions_with_version_filter(experiment):
    team = experiment.team
    user = experiment.team.members.first()

    # Create sessions with messages that have version tags
    session1 = ExperimentSessionFactory.create(experiment=experiment)
    session2 = ExperimentSessionFactory.create(experiment=experiment)
    session3 = ExperimentSessionFactory.create(experiment=experiment)

    # Add messages with version tags to sessions
    message1 = session1.chat.messages.create(message_type="ai", content="test response v1")
    message2 = session2.chat.messages.create(message_type="ai", content="test response v2")
    message3 = session3.chat.messages.create(message_type="ai", content="test response v1")

    # Create version tags and add them to messages
    session1.chat.create_and_add_tag("v1.0", team, TagCategories.EXPERIMENT_VERSION)
    message1.create_and_add_tag("v1.0", team, TagCategories.EXPERIMENT_VERSION)

    session2.chat.create_and_add_tag("v2.0", team, TagCategories.EXPERIMENT_VERSION)
    message2.create_and_add_tag("v2.0", team, TagCategories.EXPERIMENT_VERSION)

    session3.chat.create_and_add_tag("v1.0", team, TagCategories.EXPERIMENT_VERSION)
    message3.create_and_add_tag("v1.0", team, TagCategories.EXPERIMENT_VERSION)

    client = ApiTestClient(user, team)

    # Filter by v1.0 - should return session1 and session3
    response = client.get(reverse("api:session-list") + "?versions=v1.0")
    assert response.status_code == 200
    data = response.json()
    session_ids = [result["id"] for result in data["results"]]
    assert len(session_ids) == 2
    assert str(session1.external_id) in session_ids
    assert str(session3.external_id) in session_ids
    assert str(session2.external_id) not in session_ids

    # Filter by v2.0 - should return only session2
    response = client.get(reverse("api:session-list") + "?versions=v2.0")
    assert response.status_code == 200
    data = response.json()
    session_ids = [result["id"] for result in data["results"]]
    assert len(session_ids) == 1
    assert str(session2.external_id) in session_ids

    # Filter by multiple versions - should return all sessions
    response = client.get(reverse("api:session-list") + "?versions=v1.0,v2.0")
    assert response.status_code == 200
    data = response.json()
    session_ids = [result["id"] for result in data["results"]]
    assert len(session_ids) == 3


@pytest.mark.django_db()
def test_list_sessions_with_combined_filters(experiment):
    team = experiment.team
    user = experiment.team.members.first()

    # Create another experiment for testing
    experiment2 = ExperimentFactory.create(team=team)

    # Create sessions
    session1 = ExperimentSessionFactory.create(experiment=experiment)  # exp1, v1.0
    session2 = ExperimentSessionFactory.create(experiment=experiment2)  # exp2, v1.0
    session3 = ExperimentSessionFactory.create(experiment=experiment)  # exp1, v2.0

    # Add version tags
    message1 = session1.chat.messages.create(message_type="ai", content="test")
    message2 = session2.chat.messages.create(message_type="ai", content="test")
    message3 = session3.chat.messages.create(message_type="ai", content="test")

    message1.create_and_add_tag("v1.0", team, TagCategories.EXPERIMENT_VERSION)
    message2.create_and_add_tag("v1.0", team, TagCategories.EXPERIMENT_VERSION)
    message3.create_and_add_tag("v2.0", team, TagCategories.EXPERIMENT_VERSION)

    client = ApiTestClient(user, team)

    # Test combining experiment and version filters
    response = client.get(reverse("api:session-list") + f"?experiment={experiment.public_id}&versions=v1.0")
    assert response.status_code == 200
    data = response.json()
    session_ids = [result["id"] for result in data["results"]]
    # Should only return session1 (experiment1 + v1.0)
    assert len(session_ids) == 1
    assert str(session1.external_id) in session_ids


@pytest.mark.django_db()
def test_create_session(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    data = {"experiment": experiment.public_id}
    response = client.post(reverse("api:session-list"), data=data, format="json")
    response_json = response.json()
    assert response.status_code == 201, response_json
    session = ExperimentSession.objects.get(external_id=response_json["id"])
    assert response_json == get_session_json(session)


@pytest.mark.django_db()
def test_create_session_with_messages(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    data = {
        "experiment": experiment.public_id,
        "messages": [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "hello"},
        ],
    }
    response = client.post(reverse("api:session-list"), data=data, format="json")
    response_json = response.json()
    assert response.status_code == 201, response_json
    session = ExperimentSession.objects.get(external_id=response_json["id"])
    assert response_json == get_session_json(session)
    assert session.chat.messages.count() == 2


@pytest.mark.django_db()
def test_create_session_new_participant(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    data = {"experiment": experiment.public_id, "participant": "jack bean"}
    response = client.post(reverse("api:session-list"), data=data, format="json")
    response_json = response.json()
    assert response.status_code == 201, response_json
    session = ExperimentSession.objects.get(external_id=response_json["id"])
    assert session.participant.identifier == "jack bean"
    assert response_json == get_session_json(session)


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_end_experiment_session_success(auth_method, client, session):
    url = f"/api/sessions/{session.external_id}/end_experiment_session/"
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, auth_method=auth_method)
    response = client.post(url)
    assert response.status_code == status.HTTP_200_OK
    session.refresh_from_db()
    assert session.status == "pending-review"


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_update_experiment_session_state_success(auth_method, session):
    url = f"/api/sessions/{session.external_id}/update_state/"
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, auth_method=auth_method)
    new_state = {"some": "new_state", "updated": True}

    response = client.patch(url, data={"state": new_state}, format="json")

    assert response.status_code == status.HTTP_200_OK
    response_data = response.json()
    assert response_data["success"] is True
    assert response_data["state"] == new_state
    session.refresh_from_db()
    assert session.state == new_state


@pytest.mark.django_db()
def test_create_session_with_messages_and_json_state(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    state = {"status": "active", "updated_at": "2025-04-28T00:00:00Z"}
    data = {
        "experiment": experiment.public_id,
        "messages": [
            {"role": "assistant", "content": "test"},
            {"role": "user", "content": "test"},
        ],
        "state": state,
    }

    response = client.post(reverse("api:session-list"), data=data, format="json")
    response_json = response.json()

    assert response.status_code == 201, response_json
    session = ExperimentSession.objects.get(external_id=response_json["id"])
    assert session.state == state, f"Expected state {state}, but got {session.state}"
    assert response_json == get_session_json(session)
    assert session.chat.messages.count() == 2


def _create_session_request(session):
    return reverse("api:session-list"), {"experiment": str(session.experiment.public_id)}


def _end_session_request(session):
    return f"/api/sessions/{session.external_id}/end_experiment_session/", None


def _update_state_request(session):
    return f"/api/sessions/{session.external_id}/update_state/", {"state": {"injected": True}}


def _tags_request(session):
    return f"/api/sessions/{session.external_id}/tags/", {"tags": ["injected"]}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("method", "build_request"),
    [
        pytest.param("post", _create_session_request, id="create"),
        pytest.param("post", _end_session_request, id="end_experiment_session"),
        pytest.param("patch", _update_state_request, id="update_state"),
        pytest.param("post", _tags_request, id="add_tags"),
        pytest.param("delete", _tags_request, id="remove_tags"),
    ],
)
def test_read_only_key_cannot_write_to_sessions(method, build_request, session):
    """A key issued as read-only must not write through any session endpoint (ADR-0021)."""
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, read_only=True)
    url, data = build_request(session)
    original_status = session.status
    response = getattr(client, method)(url, data=data, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    session.refresh_from_db()
    assert session.status == original_status
    assert session.state == {}
    assert session.chat.tags.count() == 0
    assert ExperimentSession.objects.count() == 1


@pytest.mark.django_db()
def test_read_only_key_can_read_sessions(session):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, read_only=True)
    assert client.get(reverse("api:session-list")).status_code == 200
    assert client.get(reverse("api:session-detail", kwargs={"id": session.external_id})).status_code == 200
