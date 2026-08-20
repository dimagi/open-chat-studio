"""Authorization tests for the trigger_bot endpoints (v1 and v2).

Triggering a bot message is the API twin of the participants UI view
(``apps.participants.views.trigger_bot``), which requires ``experiments.change_participant``. The API
endpoints previously relied on the default permission stack alone, which checks team membership and
the OAuth scope but no role, so any team member could message participants on the team's channels.
"""

import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
from apps.teams.backends import (
    ANNOTATION_REVIEWER_GROUP,
    CHAT_VIEWER_GROUP,
    CHATBOT_ADMIN_GROUP,
    EVENT_ADMIN_GROUP,
    TEAM_ADMIN_GROUP,
    add_user_to_team,
    create_default_groups,
)
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

URL_NAMES = [pytest.param("api:trigger_bot", id="v1"), pytest.param("api:v2:trigger_bot", id="v2")]


@pytest.fixture()
def email_experiment(db):
    """An experiment with an email channel, so trigger_bot has a channel to dispatch on.

    ``create_default_groups()`` is called explicitly so the DB-backed groups reflect the current
    backends.py definition even though pytest runs with --reuse-db.
    """
    create_default_groups()
    experiment = ExperimentFactory.create(team=TeamFactory.create())
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
    )
    return experiment


def _request_data(experiment):
    return {
        "identifier": "participant@example.com",
        "platform": ChannelPlatform.EMAIL,
        "experiment": str(experiment.public_id),
        "message_text": "Your clinic appointment is cancelled, call +1555000000 to rebook",
    }


def _post(client, url_name, experiment):
    return client.post(reverse(url_name), json.dumps(_request_data(experiment)), content_type="application/json")


@pytest.mark.django_db()
@pytest.mark.parametrize("url_name", URL_NAMES)
@pytest.mark.parametrize(
    ("group_name", "auth_method"),
    [
        pytest.param(CHAT_VIEWER_GROUP, "api_key", id="chat-viewer-api-key"),
        pytest.param(CHAT_VIEWER_GROUP, "oauth", id="chat-viewer-oauth-token"),
        pytest.param(TEAM_ADMIN_GROUP, "api_key", id="team-admin-api-key"),
        pytest.param(EVENT_ADMIN_GROUP, "api_key", id="event-admin-api-key"),
        pytest.param(ANNOTATION_REVIEWER_GROUP, "api_key", id="annotation-reviewer-api-key"),
    ],
)
@patch("apps.api.views.channels.trigger_bot_message_task")
def test_trigger_bot_denied_without_change_participant(
    trigger_bot_message_task, email_experiment, group_name, auth_method, url_name
):
    """A member whose role lacks experiments.change_participant cannot dispatch a message."""
    user = UserFactory.create()
    add_user_to_team(email_experiment.team, user, groups=[group_name])
    client = ApiTestClient(user, email_experiment.team, auth_method=auth_method)

    response = _post(client, url_name, email_experiment)

    assert response.status_code == 403, response.content
    trigger_bot_message_task.delay_on_commit.assert_not_called()
    assert not ExperimentSession.objects.filter(experiment=email_experiment).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize("url_name", URL_NAMES)
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
@patch("apps.api.views.channels.trigger_bot_message_task")
def test_trigger_bot_allowed_with_change_participant(trigger_bot_message_task, email_experiment, auth_method, url_name):
    """A role holding experiments.change_participant (Chatbot Admin) still succeeds."""
    user = UserFactory.create()
    add_user_to_team(email_experiment.team, user, groups=[CHATBOT_ADMIN_GROUP])
    client = ApiTestClient(user, email_experiment.team, auth_method=auth_method)

    response = _post(client, url_name, email_experiment)

    assert response.status_code == 200, response.content
    trigger_bot_message_task.delay_on_commit.assert_called_once()


@pytest.mark.django_db()
@pytest.mark.parametrize("url_name", URL_NAMES)
@patch("apps.api.views.channels.trigger_bot_message_task")
def test_trigger_bot_allowed_for_machine_token_with_scope(trigger_bot_message_task, email_experiment, url_name):
    """Machine tokens have no user, so they keep relying on the chatbots:interact scope."""
    # The app owner is deliberately a non-member of the team to prove access does not depend on a
    # membership row or on a role.
    client = ApiTestClient(
        UserFactory.create(),
        email_experiment.team,
        auth_method="oauth_client_credentials",
        scopes=["chatbots:interact"],
        allowed_chatbots=[email_experiment],
    )

    response = _post(client, url_name, email_experiment)

    assert response.status_code == 200, response.content
    trigger_bot_message_task.delay_on_commit.assert_called_once()
