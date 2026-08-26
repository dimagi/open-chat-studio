"""Start-session guards for the public channel (spec D4).

The embed key is in page source, so the API enforces: only a published version is served, and a
consent-form chatbot has no live link until the consent work (step 3) ships.
"""

from unittest import mock

import pytest
from django.contrib.sites.models import Site
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from field_audit.models import AuditAction
from rest_framework.test import APIClient

from apps.api.views import chat as chat_views
from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory

TOKEN = "public_token_1234567890123456789012"
CANONICAL = "ocs.example.com"
ORIGIN = f"https://{CANONICAL}"


@pytest.fixture(autouse=True)
def _canonical_site(db):
    Site.objects.filter(id=1).update(domain=CANONICAL)
    Site.objects.clear_cache()
    yield
    Site.objects.clear_cache()


def _public_channel(team, *, consent=False, publish=True):
    experiment = ExperimentFactory.create(
        team=team, consent_form=ConsentFormFactory.create(team=team) if consent else None
    )
    if publish:
        experiment.create_new_version(make_default=True)
    return ExperimentChannelFactory.create(
        team=team, experiment=experiment, platform=ChannelPlatform.PUBLIC, extra_data={"widget_token": TOKEN}
    )


def _start(client, experiment, **body):
    return client.post(
        reverse("api:chat:start-session"),
        data={"chatbot_id": experiment.public_id, "session_data": {"source": "widget"}, **body},
        format="json",
        HTTP_X_EMBED_KEY=TOKEN,
        HTTP_ORIGIN=ORIGIN,
    )


@pytest.mark.django_db()
def test_published_public_chatbot_starts_with_a_session_token(team_with_users):
    channel = _public_channel(team_with_users)
    response = _start(APIClient(), channel.experiment)
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["session_token"]
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is True
    assert session.participant.platform == "public"


@pytest.mark.django_db()
def test_unpublished_public_chatbot_refuses_with_409(team_with_users):
    channel = _public_channel(team_with_users, publish=False)
    response = _start(APIClient(), channel.experiment)
    assert response.status_code == 409
    assert response.json()["code"] == "no_published_version"
    assert not ExperimentSession.objects.filter(experiment_channel=channel).exists()


@pytest.mark.django_db()
def test_consent_form_chatbot_refuses_with_409_until_step_3(team_with_users):
    channel = _public_channel(team_with_users, consent=True)
    response = _start(APIClient(), channel.experiment)
    assert response.status_code == 409
    assert response.json()["code"] == "consent_unavailable"


@pytest.mark.django_db()
def test_anonymous_version_number_is_refused(team_with_users):
    channel = _public_channel(team_with_users)
    response = _start(APIClient(), channel.experiment, version_number=1)
    assert response.status_code == 403


@pytest.mark.django_db()
def test_team_member_may_start_an_unpublished_public_chatbot(team_with_users):
    channel = _public_channel(team_with_users, publish=False)
    client = APIClient()
    user = team_with_users.members.first()
    client.force_login(user)
    response = _start(client, channel.experiment, participant_remote_id=user.email)
    assert response.status_code == 201, response.content


@pytest.mark.django_db()
def test_disabled_public_channel_refuses_before_the_published_check(team_with_users):
    channel = _public_channel(team_with_users, publish=False)
    channel.enabled = False
    channel.disabled_message = "Back soon"
    channel.save()
    response = _start(APIClient(), channel.experiment)
    assert response.status_code == 403
    assert "Back soon" in response.json()["error"]


def _send(client, session_id, token, text="hi"):
    return client.post(
        reverse("api:chat:send-message", kwargs={"session_id": session_id}),
        data={"message": text},
        format="json",
        HTTP_X_SESSION_TOKEN=token,
        HTTP_ORIGIN=ORIGIN,
    )


def _upload(client, session_id, token):
    return client.post(
        reverse("api:chat:upload-file", kwargs={"session_id": session_id}),
        data={"files": [SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")]},
        format="multipart",
        HTTP_X_SESSION_TOKEN=token,
        HTTP_ORIGIN=ORIGIN,
    )


def _unpublish(experiment):
    experiment.versions.update(is_default_version=False, audit_action=AuditAction.AUDIT)


@pytest.mark.django_db()
def test_send_refuses_once_the_published_version_is_gone(team_with_users):
    channel = _public_channel(team_with_users)
    client = APIClient()
    started = _start(client, channel.experiment).json()
    _unpublish(channel.experiment)
    response = _send(client, started["session_id"], started["session_token"])
    assert response.status_code == 409
    assert response.json()["code"] == "no_published_version"


@pytest.mark.django_db()
def test_upload_refuses_once_the_published_version_is_gone(team_with_users):
    channel = _public_channel(team_with_users)
    client = APIClient()
    started = _start(client, channel.experiment).json()
    _unpublish(channel.experiment)
    response = _upload(client, started["session_id"], started["session_token"])
    assert response.status_code == 409
    assert response.json()["code"] == "no_published_version"


@pytest.mark.django_db()
def test_team_member_can_send_on_an_unpublished_public_link(team_with_users, monkeypatch):
    channel = _public_channel(team_with_users, publish=False)
    user = team_with_users.members.first()
    monkeypatch.setattr(
        chat_views.get_response_for_webchat_task, "delay", lambda *a, **k: mock.Mock(task_id="member-preview")
    )
    client = APIClient()
    client.force_login(user)
    started = _start(client, channel.experiment, participant_remote_id=user.email)
    assert started.status_code == 201, started.content
    body = started.json()
    response = _send(client, body["session_id"], body["session_token"])
    assert response.status_code == 202, response.content


@pytest.mark.django_db()
def test_send_on_a_live_public_session_uses_the_published_version(team_with_users, monkeypatch):
    channel = _public_channel(team_with_users)
    seen = {}

    def fake_delay(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return mock.Mock(task_id="public-send-test-task")

    monkeypatch.setattr(chat_views.get_response_for_webchat_task, "delay", fake_delay)
    client = APIClient()
    started = _start(client, channel.experiment).json()
    response = _send(client, started["session_id"], started["session_token"])
    assert response.status_code == 202, response.content
    published = channel.experiment.versions.get(is_default_version=True)
    assert seen["kwargs"]["experiment_id"] == published.id


@pytest.mark.django_db()
def test_send_refuses_once_a_consent_form_is_published(team_with_users):
    channel = _public_channel(team_with_users)
    client = APIClient()
    started = _start(client, channel.experiment).json()
    working = channel.experiment
    working.consent_form = ConsentFormFactory.create(team=team_with_users)
    working.save()
    working.create_new_version(make_default=True)
    response = _send(client, started["session_id"], started["session_token"])
    assert response.status_code == 409
    assert response.json()["code"] == "consent_unavailable"


@pytest.mark.django_db()
def test_regeneration_revokes_the_old_key_and_the_live_session(team_with_users):
    channel = _public_channel(team_with_users)
    client = APIClient()
    started = _start(client, channel.experiment).json()
    channel.extra_data["widget_token"] = "public_token_regenerated_00000000000"
    channel.save()
    channel.end_live_sessions()
    assert _start(client, channel.experiment).status_code == 401
    response = _send(client, started["session_id"], started["session_token"])
    assert response.status_code == 400
    assert "ended" in response.json()["error"]
