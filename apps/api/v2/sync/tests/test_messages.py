"""GET /api/v2/messages/: incremental, team-scoped message sync (ADR-0072)."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.annotations import TagFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

URL = reverse("api:v2:message-list")
BASE = timezone.now() - timedelta(days=1)


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client(team):
    return ApiTestClient(team.members.first(), team)


def _session(team):
    return ExperimentSessionFactory.create(team=team, experiment=ExperimentFactory.create(team=team))


def _message(session, minutes=0, **kwargs):
    """A message last updated ``minutes`` after BASE, well outside the settle window."""
    kwargs.setdefault("message_type", ChatMessageType.HUMAN)
    message = ChatMessage.objects.create(chat=session.chat, content="hi", **kwargs)
    ChatMessage.objects.filter(pk=message.pk).update(updated_at=BASE + timedelta(minutes=minutes))
    return message


def _ids(response):
    assert response.status_code == 200, response.content
    return [row["id"] for row in response.json()["results"]]


@pytest.mark.django_db()
def test_unauthenticated_is_refused():
    assert APIClient().get(URL).status_code == 401


@pytest.mark.django_db()
def test_returns_the_team_s_messages_with_internal_metadata_removed(team, client):
    session = _session(team)
    later = _message(session, minutes=2, message_type=ChatMessageType.AI)
    earlier = _message(session, minutes=1, metadata={"compression_marker": "x", "openai_run_id": "run_1"})
    _message(_session(TeamWithUsersFactory.create()))

    response = client.get(URL)

    assert _ids(response) == [earlier.id, later.id]
    row = response.json()["results"][0]
    assert row == {
        "id": earlier.id,
        "session_id": str(session.external_id),
        "role": "user",
        "content": "hi",
        "metadata": {"compression_marker": "x"},
        "tags": [],
        "attachments": [],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    assert response.json()["results"][1]["role"] == "assistant"


@pytest.mark.django_db()
def test_cursor_resumes_and_is_kept_once_caught_up(team, client):
    session = _session(team)
    messages = [_message(session, minutes=i) for i in range(3)]

    first = client.get(URL, {"limit": 2}).json()
    second = client.get(URL, {"limit": 2, "cursor": first["cursor"]}).json()
    caught_up = client.get(URL, {"limit": 2, "cursor": second["cursor"]}).json()

    assert [row["id"] for row in first["results"] + second["results"]] == [m.id for m in messages]
    assert caught_up == {"cursor": second["cursor"], "has_more": False, "results": []}


@pytest.mark.django_db()
def test_messages_inside_the_settle_window_are_held_back(team, client):
    session = _session(team)
    settled = _message(session)
    ChatMessage.objects.create(chat=session.chat, content="just now", message_type=ChatMessageType.HUMAN)

    assert _ids(client.get(URL)) == [settled.id]


@pytest.mark.django_db()
@pytest.mark.parametrize("param", ["chatbot", "session", "participant"])
def test_filters(team, client, param):
    wanted_session = _session(team)
    wanted = _message(wanted_session)
    _message(_session(team))
    value = {
        "chatbot": wanted_session.experiment.public_id,
        "session": wanted_session.external_id,
        "participant": wanted_session.participant.public_id,
    }[param]

    assert _ids(client.get(URL, {param: str(value)})) == [wanted.id]


@pytest.mark.django_db()
@pytest.mark.parametrize("all_data", [pytest.param(False, id="listed-chatbots"), pytest.param(True, id="all-data")])
def test_machine_token_reads_only_the_chatbots_it_reaches(team, all_data):
    listed_session = _session(team)
    listed = _message(listed_session)
    unlisted = _message(_session(team))
    client = ApiTestClient(
        team.members.first(),
        team,
        auth_method="oauth_client_credentials",
        scopes=["sessions:read"],
        allowed_chatbots=[listed_session.experiment],
        all_data=all_data,
    )

    assert set(_ids(client.get(URL))) == ({listed.id, unlisted.id} if all_data else {listed.id})


@pytest.mark.django_db()
def test_attachments_and_tags_load_in_the_same_queries_whatever_the_page_size(team, client):
    tag = TagFactory.create(team=team, name="refund")

    def queries_for(count):
        for _ in range(count):
            session = _session(team)
            file = FileFactory.create(team=team)
            session.chat.attach_files("ocs_attachments", [file])
            message = _message(session, metadata={"ocs_attachment_file_ids": [file.id]})
            message.add_tag(tag, team=team)
        ChatMessage.objects.filter(chat__team=team).update(updated_at=BASE)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(URL, {"limit": 100})
        rows = response.json()["results"]
        assert all(len(row["attachments"]) == 1 and row["tags"] == ["refund"] for row in rows)
        return len(queries)

    assert queries_for(2) == queries_for(6)
