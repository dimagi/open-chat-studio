from datetime import timedelta
from unittest import mock

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.api.session_tokens import issue_session_token
from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
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
        task.delay.return_value = mock.Mock(task_id="send-msg-test-unique-456")
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
def test_legacy_non_public_session_denied_for_unknown_participant(api_client, experiment):
    experiment.participant_allowlist = ["someone@example.com"]
    experiment.save(update_fields=["participant_allowlist"])
    legacy = ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)
    assert api_client.get(poll_url(legacy)).status_code == 403


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
def test_team_member_without_participant_denied(api_client, session, team_with_users):
    """Team membership alone does not grant token-free access; only the session's
    own participant-user bypasses the token."""
    api_client.force_login(team_with_users.members.first())
    assert api_client.get(poll_url(session)).status_code == 403


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


def start_session(api_client, experiment, data_extra=None, **request_kwargs):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, **(data_extra or {})}
    return api_client.post(url, data=data, format="json", **request_kwargs)


@pytest.mark.django_db()
def test_start_session_issues_token_by_default(api_client, experiment):
    response = start_session(api_client, experiment)
    assert response.status_code == 201
    body = response.json()
    assert body["session_token"]
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is True
    # the issued token grants access
    url = reverse("api:chat:poll-response", kwargs={"session_id": body["session_id"]})
    assert api_client.get(url, HTTP_X_SESSION_TOKEN=body["session_token"]).status_code == 200


@pytest.mark.django_db()
def test_start_session_explicit_opt_out(api_client, experiment):
    response = start_session(api_client, experiment, {"use_session_token": False})
    body = response.json()
    assert body["session_token"] is None
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is False


@pytest.mark.django_db()
def test_start_session_explicit_opt_in_with_widget_header(api_client, experiment):
    response = start_session(api_client, experiment, {"use_session_token": True}, HTTP_X_OCS_WIDGET_VERSION="0.9.0")
    body = response.json()
    assert body["session_token"]
    assert ExperimentSession.objects.get(external_id=body["session_id"]).session_token_required is True


@pytest.mark.django_db()
def test_old_widget_implicitly_opts_out(api_client, experiment):
    """Pre-token widgets send the version header but no use_session_token field."""
    response = start_session(api_client, experiment, HTTP_X_OCS_WIDGET_VERSION="0.8.0")
    body = response.json()
    assert body["session_token"] is None
    assert ExperimentSession.objects.get(external_id=body["session_id"]).session_token_required is False


@pytest.mark.django_db()
def test_authenticated_start_then_poll_without_token(api_client, experiment, team_with_users):
    """Authenticated users rely on the auth bypass, not the returned token."""
    user = team_with_users.members.first()
    api_client.force_login(user)
    response = start_session(api_client, experiment, {"participant_remote_id": user.email})
    assert response.status_code == 201
    body = response.json()
    assert body["session_token"]  # token still issued
    url = reverse("api:chat:poll-response", kwargs={"session_id": body["session_id"]})
    assert api_client.get(url).status_code == 200  # no token header needed


@pytest.mark.django_db()
def test_opted_out_session_polls_anonymously(api_client, experiment):
    response = start_session(api_client, experiment, {"use_session_token": False})
    assert response.status_code == 201
    body = response.json()
    url = reverse("api:chat:poll-response", kwargs={"session_id": body["session_id"]})
    assert api_client.get(url).status_code == 200  # legacy access, no token
