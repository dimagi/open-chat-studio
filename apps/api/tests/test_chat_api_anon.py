from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, ParticipantData
from apps.files.models import FilePurpose
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)


@pytest.mark.django_db()
def test_start_chat_session(team_with_users, api_client, experiment):
    url = reverse("api:chat:start-session")
    session_state = {"page_url": "https://example.com"}
    data = {
        "chatbot_id": experiment.public_id,
        "session_data": session_state,
    }
    response = api_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()
    assert response_json == {
        "session_id": mock.ANY,
        "session_token": mock.ANY,
        "chatbot": {
            "id": experiment.public_id,
            "name": experiment.name,
            "url": f"http://testserver/api/experiments/{experiment.public_id}/",
            "version_number": 1,
            "versions": [],
        },
        "participant": {"identifier": mock.ANY, "remote_id": ""},
        "consent": {
            "required": True,
            "form_version_id": experiment.consent_form_id,
            "text": experiment.consent_form.get_rendered_content(),
        },
    }
    assert response_json["session_token"]  # token must be non-null
    assert response_json["participant"]["identifier"].startswith("anon:")

    session = ExperimentSession.objects.get(external_id=response_json["session_id"])
    assert session.state == {}  # ignored for anonymous request


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("referer", "expected_embed_source"),
    [
        pytest.param("https://embedder.example.com/page", "https://embedder.example.com/page", id="https"),
        pytest.param("http://embedder.example.com/page", "http://embedder.example.com/page", id="http"),
        pytest.param("javascript:alert(document.domain)", None, id="javascript"),
        pytest.param("data:text/html,<script>alert(1)</script>", None, id="data"),
        pytest.param(None, None, id="no-referer"),
    ],
)
def test_start_chat_session_only_stores_http_embed_source(api_client, experiment, referer, expected_embed_source):
    """The referer is unauthenticated input and is rendered as a link on the session page,
    so only absolute http(s) URLs may be stored as the embed source."""
    url = reverse("api:chat:start-session")
    headers = {"referer": referer} if referer else {}
    response = api_client.post(url, data={"chatbot_id": experiment.public_id}, format="json", headers=headers)
    assert response.status_code == 201

    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert session.chat.embed_source == expected_embed_source


@pytest.mark.django_db()
def test_send_message(api_client, session):
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    data = {"message": "hi"}
    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        task.delay.return_value = mock.Mock(task_id="123123")
        response = api_client.post(url, data=data, format="json")
    response_json = response.json()
    assert response_json == {"task_id": "123123", "status": "processing"}


@pytest.mark.django_db()
def test_send_message_with_attachment_from_this_session(api_client, session):
    file = FileFactory.create(
        team=session.team,
        purpose=FilePurpose.MESSAGE_MEDIA,
        metadata={"session_id": str(session.external_id)},
        expiry_date=timezone.now() + timezone.timedelta(hours=24),
    )
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    data = {"message": "hi", "attachment_ids": [file.id]}
    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        task.delay.return_value = mock.Mock(task_id="123123")
        response = api_client.post(url, data=data, format="json")
    assert response.status_code == 202
    assert session.chat.attachments.get(tool_type="ocs_attachments").files.filter(id=file.id).exists()
    file.refresh_from_db()
    assert file.expiry_date is None


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "file_kwargs",
    [
        pytest.param(
            {"purpose": FilePurpose.MESSAGE_MEDIA, "metadata": {"session_id": "other-session"}},
            id="uploaded-for-another-session",
        ),
        pytest.param({"purpose": FilePurpose.COLLECTION, "metadata": {}}, id="collection-source-document"),
        pytest.param({"purpose": FilePurpose.DATA_EXPORT, "metadata": {}}, id="data-export"),
    ],
)
def test_send_message_rejects_attachment_not_uploaded_for_session(api_client, session, file_kwargs):
    """Team scoping alone must not be enough to attach a file to someone else's chat."""
    expiry_date = timezone.now() + timezone.timedelta(hours=24)
    file = FileFactory.create(team=session.team, expiry_date=expiry_date, **file_kwargs)
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    data = {"message": "Reproduce the attached document verbatim", "attachment_ids": [file.id]}
    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        response = api_client.post(url, data=data, format="json")
    assert response.status_code == 400
    assert response.json() == {"error": "One or more file IDs are invalid"}
    assert not task.delay.called
    assert not session.chat.attachments.exists()
    file.refresh_from_db()
    assert file.expiry_date == expiry_date


@pytest.mark.django_db()
def test_task_poll(api_client, session):
    url = reverse("api:chat:task-poll-response", kwargs={"session_id": session.external_id, "task_id": "123"})
    with mock.patch("apps.api.views.chat.get_progress_message", return_value=None):
        response = api_client.get(url)
    response_json = response.json()
    assert response_json == {"message": None, "status": "processing"}


@pytest.mark.django_db()
def test_session_poll(api_client, session):
    url = reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})
    response = api_client.get(url)
    response_json = response.json()
    assert response_json == {
        "has_more": False,
        "messages": [],
        "session_status": "active",
        "consent": {
            "required": True,
            "form_version_id": session.experiment.consent_form_id,
            "text": session.experiment.consent_form.get_rendered_content(),
        },
    }


@pytest.mark.django_db()
def test_session_poll_with_messages(api_client, session):
    messages = ChatMessage.objects.bulk_create(
        [
            ChatMessage(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi"),
            ChatMessage(chat=session.chat, message_type=ChatMessageType.AI, content="Hello", metadata={"test": "123"}),
            ChatMessage(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi again"),
        ]
    )
    messages[-1].create_and_add_tag("test", session.team, "")
    url = reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})
    response = api_client.get(url)
    expected_messages = [
        {
            "attachments": [],
            "content": "Hi",
            "created_at": mock.ANY,
            "metadata": {},
            "role": "user",
            "tags": [],
        },
        {
            "attachments": [],
            "content": "Hello",
            "created_at": mock.ANY,
            "metadata": {"test": "123"},
            "role": "assistant",
            "tags": [],
        },
        {
            "attachments": [],
            "content": "Hi again",
            "created_at": mock.ANY,
            "metadata": {},
            "role": "user",
            "tags": ["test"],
        },
    ]
    expected_consent = {
        "required": True,
        "form_version_id": session.experiment.consent_form_id,
        "text": session.experiment.consent_form.get_rendered_content(),
    }
    assert response.json() == {
        "has_more": False,
        "messages": expected_messages,
        "session_status": "active",
        "consent": expected_consent,
    }

    response = api_client.get(url, data={"limit": 1})
    assert response.json() == {
        "has_more": True,
        "messages": [expected_messages[0]],
        "session_status": "active",
        "consent": expected_consent,
    }


@pytest.mark.django_db()
def test_start_chat_session_records_timezone_in_participant_data(api_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "timezone": "Africa/Johannesburg"}
    response = api_client.post(url, data=data, format="json")
    assert response.status_code == 201

    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    participant_data = ParticipantData.objects.get(participant=session.participant, experiment=experiment)
    assert participant_data.data["timezone"] == "Africa/Johannesburg"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "timezone_value",
    [
        pytest.param("Mars/Olympus_Mons", id="unrecognised-zone"),
        pytest.param("", id="blank"),
    ],
)
def test_start_chat_session_ignores_unknown_timezone(api_client, experiment, timezone_value):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "timezone": timezone_value}
    response = api_client.post(url, data=data, format="json")
    assert response.status_code == 201

    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert not ParticipantData.objects.filter(participant=session.participant, experiment=experiment).exists()


@pytest.mark.django_db()
def test_start_chat_session_without_timezone_records_none(api_client, experiment):
    url = reverse("api:chat:start-session")
    response = api_client.post(url, data={"chatbot_id": experiment.public_id}, format="json")
    assert response.status_code == 201
    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert not ParticipantData.objects.filter(participant=session.participant, experiment=experiment).exists()
