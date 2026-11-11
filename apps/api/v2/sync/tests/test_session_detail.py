"""GET /api/v2/sessions/{id}/: one session with its messages embedded (ADR-0072)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.annotations import TagFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


def _url(session):
    return reverse("api:v2:session-detail", kwargs={"id": str(session.external_id)})


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client(team):
    return ApiTestClient(team.members.first(), team)


def _session(team):
    return ExperimentSessionFactory.create(team=team, experiment=ExperimentFactory.create(team=team))


def _message(session, content, message_type=ChatMessageType.HUMAN):
    return ChatMessage.objects.create(chat=session.chat, content=content, message_type=message_type)


@pytest.mark.django_db()
def test_returns_the_session_with_its_messages_in_order(team, client):
    session = _session(team)
    first = _message(session, "hi")
    second = _message(session, "hello", ChatMessageType.AI)
    session.chat.add_tag(TagFactory.create(team=team, name="billing"), team=team)

    response = client.get(_url(session))

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["id"] == str(session.external_id)
    assert body["tags"] == ["billing"]
    assert [(m["id"], m["role"]) for m in body["messages"]] == [(first.id, "user"), (second.id, "assistant")]


@pytest.mark.django_db()
def test_another_team_s_session_is_not_found(client):
    assert client.get(_url(_session(TeamWithUsersFactory.create()))).status_code == 404


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("listed", "status"),
    [pytest.param(True, 200, id="listed"), pytest.param(False, 404, id="unlisted")],
)
def test_machine_token_reads_only_the_chatbots_it_reaches(team, listed, status):
    session = _session(team)
    client = ApiTestClient(
        team.members.first(),
        team,
        auth_method="oauth_client_credentials",
        scopes=["sessions:read"],
        allowed_chatbots=[session.experiment] if listed else [],
    )

    assert client.get(_url(session)).status_code == status


@pytest.mark.django_db()
def test_queries_do_not_grow_with_the_messages(team, client):
    tag = TagFactory.create(team=team)

    def queries_for(count):
        session = _session(team)
        for i in range(count):
            file = FileFactory.create(team=team)
            session.chat.attach_files("ocs_attachments", [file])
            message = ChatMessage.objects.create(
                chat=session.chat,
                content=f"m{i}",
                message_type=ChatMessageType.HUMAN,
                metadata={"ocs_attachment_file_ids": [file.id]},
            )
            message.add_tag(tag, team=team)
        with CaptureQueriesContext(connection) as queries:
            assert client.get(_url(session)).status_code == 200
        return len(queries)

    assert queries_for(2) == queries_for(6)
