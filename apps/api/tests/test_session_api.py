import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.fields import DateTimeField

from apps.annotations.models import Tag, TagCategories
from apps.chat.models import ChatAttachment
from apps.experiments.models import ExperimentSession, Team
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


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
    }


def get_session_json(session, expected_messages=None, expected_tags=None):
    experiment = session.experiment
    data = {
        "url": f"http://testserver/api/sessions/{session.external_id}/",
        "experiment": {
            "id": str(experiment.public_id),
            "name": experiment.name,
            "url": f"http://testserver/api/experiments/{experiment.public_id}/",
            "version_number": 1,
            "versions": [],
        },
        "participant": {"identifier": session.participant.identifier, "remote_id": ""},
        "id": str(session.external_id),
        "team": {
            "name": session.team.name,
            "slug": session.team.slug,
        },
        "created_at": DateTimeField().to_representation(session.created_at),
        "updated_at": DateTimeField().to_representation(session.updated_at),
        "tags": expected_tags if expected_tags is not None else [],
    }
    if expected_messages is not None:
        data["messages"] = expected_messages
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


def _create_attachments(chat, message):
    tool_resource, _created = ChatAttachment.objects.get_or_create(
        chat_id=chat.id,
        tool_type="file_search",
    )
    file_ids = ["file_1", "file_2"]
    files = []
    for external_id in file_ids:
        files.append(FileFactory(name=external_id, external_id=external_id))
    tool_resource.files.add(*files)
    message.metadata = {"openai_file_ids": file_ids}
    message.save()
    return files


@pytest.mark.django_db()
def test_list_sessions_with_experiment_filter(experiment):
    team = experiment.team
    user = experiment.team.members.first()

    # Create another experiment in the same team
    experiment2 = ExperimentFactory(team=team)

    # Create sessions for both experiments
    session1 = ExperimentSessionFactory(experiment=experiment)
    session2 = ExperimentSessionFactory(experiment=experiment2)
    session3 = ExperimentSessionFactory(experiment=experiment)

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
    }


@pytest.mark.django_db()
def test_list_sessions_with_version_filter(experiment):
    team = experiment.team
    user = experiment.team.members.first()

    # Create sessions with messages that have version tags
    session1 = ExperimentSessionFactory(experiment=experiment)
    session2 = ExperimentSessionFactory(experiment=experiment)
    session3 = ExperimentSessionFactory(experiment=experiment)

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
    from apps.annotations.models import TagCategories

    team = experiment.team
    user = experiment.team.members.first()

    # Create another experiment for testing
    experiment2 = ExperimentFactory(team=team)

    # Create sessions
    session1 = ExperimentSessionFactory(experiment=experiment)  # exp1, v1.0
    session2 = ExperimentSessionFactory(experiment=experiment2)  # exp2, v1.0
    session3 = ExperimentSessionFactory(experiment=experiment)  # exp1, v2.0

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
    team = Team.objects.create(name="Test Team")
    session.team = team
    session.save()
    url = f"/api/sessions/{session.external_id}/end_experiment_session/"
    user = session.experiment.team.members.first()
    client = ApiTestClient(user, session.experiment.team, auth_method=auth_method)
    response = client.post(url)
    assert response.status_code == status.HTTP_200_OK
    session.refresh_from_db()
    assert session.status == "pending-review"


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_update_experiment_session_state_success(auth_method, session):
    team = Team.objects.create(name="Test Team")
    session.team = team
    session.save()
    url = f"/api/sessions/{session.external_id}/update_state/"
    user = session.experiment.team.members.first()
    client = ApiTestClient(user, session.experiment.team, auth_method=auth_method)
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
