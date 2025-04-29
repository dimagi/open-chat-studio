import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.fields import DateTimeField

from apps.annotations.models import Tag
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
def test_list_sessions(session):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team)
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
    expected_results = [get_session_json(session2), get_session_json(session1)]
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": expected_results,
    }

    # Remove filters by tag
    response = client.get(reverse("api:session-list"))
    expected_results = [get_session_json(sessions[2]), get_session_json(session2), get_session_json(session1)]
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": expected_results,
    }


def get_session_json(session, expected_messages=None):
    experiment = session.experiment
    data = {
        "url": f"http://testserver/api/sessions/{session.external_id}/",
        "experiment": {
            "id": str(experiment.public_id),
            "name": experiment.name,
            "url": f"http://testserver/api/experiments/{experiment.public_id}/",
            "version_number": 1,
        },
        "participant": {"identifier": session.participant.identifier},
        "id": str(session.external_id),
        "team": {
            "name": session.team.name,
            "slug": session.team.slug,
        },
        "created_at": DateTimeField().to_representation(session.created_at),
        "updated_at": DateTimeField().to_representation(session.updated_at),
    }
    if expected_messages is not None:
        data["messages"] = expected_messages
    return data


@pytest.mark.django_db()
def test_retrieve_session(session):
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

    message = session.chat.messages.create(message_type="human", content="rabbit in a hat", summary="Abracadabra")
    message.add_tag(tags[0], session.team, user)
    message.add_tag(tags[1], session.team, user)

    client = ApiTestClient(user, session.team)
    response = client.get(reverse("api:session-detail", kwargs={"id": session.external_id}))
    assert response.status_code == 200
    response_json = response.json()

    for message in response_json.get("messages", []):
        message["created_at"] = "fake date"

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
                "metadata": {"is_summary": True},
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
def test_end_experiment_session_success(client, session):
    team = Team.objects.create(name="Test Team")
    session.team = team
    session.save()
    url = f"/api/sessions/{session.external_id}/end_experiment_session/"
    user = session.experiment.team.members.first()
    client = ApiTestClient(user, session.experiment.team)
    response = client.post(url)
    assert response.status_code == status.HTTP_200_OK
    session.refresh_from_db()
    assert session.status == "pending-review"


@pytest.mark.django_db()
def test_update_experiment_session_state_success(session):
    team = Team.objects.create(name="Test Team")
    session.team = team
    session.save()
    url = f"/api/sessions/{session.external_id}/update_state/"
    user = session.experiment.team.members.first()
    client = ApiTestClient(user, session.experiment.team)
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
