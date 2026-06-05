from datetime import timedelta
from unittest import mock

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.api.session_tokens import issue_session_token
from apps.channels.models import ChannelPlatform
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def session(experiment):
    # session_token_required defaults to True
    return ExperimentSessionFactory.create(experiment=experiment)


@pytest.fixture()
def token(session):
    return issue_session_token(session)


def poll_url(session):
    return reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})


@pytest.mark.django_db()
def test_poll_without_token_denied(api_client, session):
    response = api_client.get(poll_url(session))
    assert response.status_code == 403
    assert response.json()["code"] == "session_token_required"


@pytest.mark.django_db()
def test_poll_with_token_allowed(api_client, session, token):
    response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=token)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_poll_with_invalid_token_denied(api_client, session, token):
    response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=token[:-2] + "xx")
    assert response.status_code == 403
    assert response.json()["code"] == "session_token_invalid"


@pytest.mark.django_db()
def test_token_for_other_session_denied(api_client, session):
    other = ExperimentSessionFactory.create(experiment=session.experiment)
    response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=issue_session_token(other))
    assert response.status_code == 403
    assert response.json()["code"] == "session_token_invalid"


@pytest.mark.django_db()
def test_inactive_session_expired(api_client, session, token):
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=token)
    assert response.status_code == 403
    assert response.json()["code"] == "session_expired"


@pytest.mark.django_db()
def test_send_message_requires_token(api_client, session, token):
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    assert api_client.post(url, data={"message": "hi"}, format="json").status_code == 403
    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        task.delay.return_value = mock.Mock(task_id="123")
        response = api_client.post(url, data={"message": "hi"}, format="json", HTTP_X_SESSION_TOKEN=token)
    assert response.status_code == 202


@pytest.mark.django_db()
def test_task_poll_requires_token(api_client, session, token):
    url = reverse("api:chat:task-poll-response", kwargs={"session_id": session.external_id, "task_id": "123"})
    assert api_client.get(url).status_code == 403
    with mock.patch("apps.api.views.chat.get_progress_message", return_value=None):
        assert api_client.get(url, HTTP_X_SESSION_TOKEN=token).status_code == 200


@pytest.mark.django_db()
def test_upload_requires_token(api_client, session):
    url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
    response = api_client.post(url, data={})
    assert response.status_code == 403


@pytest.mark.django_db()
def test_legacy_session_skips_token(api_client, experiment):
    legacy = ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)
    assert api_client.get(poll_url(legacy)).status_code == 200


@pytest.mark.django_db()
def test_participant_user_bypasses_token(api_client, session):
    user = session.participant.user
    if user is None:
        user = UserFactory.create()
        session.participant.user = user
        session.participant.save()
    api_client.force_login(user)
    assert api_client.get(poll_url(session)).status_code == 200


@pytest.mark.django_db()
def test_team_member_bypasses_token(api_client, session, team_with_users):
    api_client.force_login(team_with_users.members.first())
    assert api_client.get(poll_url(session)).status_code == 200


@pytest.mark.django_db()
def test_unrelated_user_denied(api_client, session):
    api_client.force_login(UserFactory.create())
    response = api_client.get(poll_url(session))
    assert response.status_code == 403


@pytest.mark.django_db()
def test_embed_key_alone_does_not_bypass_token(api_client, experiment):
    channel = ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "test_widget_token_123456789012", "allowed_domains": ["example.com"]},
    )
    session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)
    response = api_client.get(
        poll_url(session),
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
        HTTP_ORIGIN="https://example.com",
    )
    assert response.status_code == 403
    # but with the token as well it works
    response = api_client.get(
        poll_url(session),
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
        HTTP_ORIGIN="https://example.com",
        HTTP_X_SESSION_TOKEN=issue_session_token(session),
    )
    assert response.status_code == 200
