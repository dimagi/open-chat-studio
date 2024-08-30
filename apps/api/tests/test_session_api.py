import pytest
from django.urls import reverse
from rest_framework.fields import DateTimeField

from apps.annotations.models import Tag
from apps.chat.models import ChatAttachment
from apps.experiments.models import ExperimentSession
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


def get_session_json(session, expected_messages=None):
    experiment = session.experiment
    data = {
        "url": f"http://testserver/api/sessions/{session.external_id}/",
        "experiment": {
            "id": str(experiment.public_id),
            "name": experiment.name,
            "url": f"http://testserver/api/experiments/{experiment.public_id}/",
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
    assert response.json() == get_session_json(
        session,
        expected_messages=[
            {"role": "assistant", "content": "hi", "metadata": {}, "tags": [], "attachments": []},
            {
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
                "role": "system",
                "content": "Abracadabra",
                "metadata": {"is_summary": True},
                "tags": [],
                "attachments": [],
            },
            {
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
