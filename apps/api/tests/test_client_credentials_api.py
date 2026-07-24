"""End-to-end API tests for client-credentials (machine) OAuth tokens.

A machine token is pinned to a team via its Application, has no user, and no Membership row. These
tests exercise the acceptance criteria: read endpoints resolve request.team from the token alone,
team isolation holds, and user-only surfaces (/me) are refused.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.chat.models import ChatMessage
from apps.experiments.models import Participant
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def session(db):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    return ExperimentSessionFactory.create(experiment=experiment)


def _machine_client(team, scopes):
    # The app owner is deliberately a non-member of the pinned team to prove access does not depend
    # on any membership row.
    return ApiTestClient(UserFactory.create(), team, auth_method="oauth_client_credentials", scopes=scopes)


@pytest.mark.django_db()
def test_machine_token_lists_sessions_without_membership(session):
    client = _machine_client(session.team, scopes=["sessions:read"])
    response = client.get(reverse("api:session-list"))
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.django_db()
def test_machine_token_lists_chatbots_without_membership(session):
    client = _machine_client(session.team, scopes=["chatbots:read"])
    response = client.get(reverse("api:v2:chatbot-list"))
    assert response.status_code == 200
    ids = {result["id"] for result in response.json()["results"]}
    assert str(session.experiment.public_id) in ids


@pytest.mark.django_db()
def test_machine_token_is_isolated_to_its_pinned_team(session):
    """A token pinned to a different team sees none of this team's data."""
    other_team = TeamWithUsersFactory.create()
    client = _machine_client(other_team, scopes=["sessions:read"])
    response = client.get(reverse("api:session-list"))
    assert response.status_code == 200
    assert response.json()["count"] == 0


@pytest.mark.django_db()
def test_machine_token_me_endpoint_is_forbidden(session):
    """/me is user-specific and has no meaning for a machine token."""
    client = _machine_client(session.team, scopes=["sessions:read"])
    response = client.get(reverse("api:v2:me"))
    assert response.status_code == 403


@pytest.mark.django_db()
def test_machine_token_missing_scope_is_forbidden(session):
    """Authorization rests on the OAuth scope: a sessions-only token cannot read chatbots."""
    client = _machine_client(session.team, scopes=["sessions:read"])
    response = client.get(reverse("api:v2:chatbot-list"))
    assert response.status_code == 403


@pytest.mark.django_db()
def test_machine_token_creates_session_with_null_actor(session):
    """A machine write must not attribute to a user (no AnonymousUser assigned to FK fields)."""
    client = _machine_client(session.team, scopes=["sessions:write", "sessions:read"])
    response = client.post(
        reverse("api:session-list"),
        data={"experiment": str(session.experiment.public_id), "participant": "p-machine"},
        format="json",
    )
    assert response.status_code == 201, response.content


@pytest.mark.django_db()
def test_machine_token_create_session_without_participant_is_400(session):
    """No user means no email fallback, so the participant identifier must be supplied explicitly."""
    client = _machine_client(session.team, scopes=["sessions:write", "sessions:read"])
    response = client.post(
        reverse("api:session-list"),
        data={"experiment": str(session.experiment.public_id)},
        format="json",
    )
    assert response.status_code == 400, response.content


@pytest.mark.django_db()
def test_machine_token_adds_session_tag(session):
    client = _machine_client(session.team, scopes=["sessions:write", "sessions:read"])
    response = client.post(
        reverse("api:session-tags", kwargs={"id": str(session.external_id)}),
        data={"tags": ["machine-tag"]},
        format="json",
    )
    assert response.status_code == 200, response.content


@pytest.mark.django_db()
def test_machine_token_deletes_participant_schedule(session):
    client = _machine_client(session.team, scopes=["participants:write", "participants:read"])
    payload = {
        "identifier": "p-machine",
        "platform": "api",
        "data": [
            {
                "experiment": str(session.experiment.public_id),
                "schedules": [{"id": "sched-x", "delete": True}],
            }
        ],
    }
    response = client.post(reverse("api:participant-data"), data=payload, format="json")
    assert response.status_code == 200, response.content


@pytest.mark.django_db()
def test_machine_token_reads_usage(session):
    """usage:read works for a machine token (no user-permission gate)."""
    client = _machine_client(session.team, scopes=["usage:read"])
    response = client.get(reverse("api:v2:usage"), {"metric": "sessions", "period": "current_month"})
    assert response.status_code == 200, response.content


@pytest.mark.django_db()
@patch("apps.api.openai.handle_api_message")
def test_machine_token_chat_completion_uses_user_field_as_participant(mock_handle, session):
    """chatbots:interact: the OpenAI `user` field identifies the (user-less) participant."""
    mock_handle.return_value = ChatMessage(content="ok")
    client = _machine_client(session.team, scopes=["chatbots:interact"])
    url = reverse("api:openai-chat-completions", kwargs={"experiment_id": session.experiment.public_id})

    response = client.post(
        url,
        data={"messages": [{"role": "user", "content": "hi"}], "user": "machine-participant"},
        format="json",
    )

    assert response.status_code == 200, response.content
    participant = Participant.objects.get(identifier="machine-participant", team=session.team)
    assert participant.user is None
    # The messaging layer receives None, never the AnonymousUser.
    assert mock_handle.call_args[0][0] is None


@pytest.mark.django_db()
def test_machine_token_chat_completion_requires_user_field(session):
    """Without an authenticated user and no `user` field, there is no participant identifier."""
    client = _machine_client(session.team, scopes=["chatbots:interact"])
    url = reverse("api:openai-chat-completions", kwargs={"experiment_id": session.experiment.public_id})

    response = client.post(url, data={"messages": [{"role": "user", "content": "hi"}]}, format="json")

    assert response.status_code == 400, response.content
