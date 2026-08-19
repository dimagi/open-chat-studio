"""A client-credentials application reaches only the chatbots it was pinned to.

`chatbots:interact` is team-scoped, so on its own it lets a machine token converse with every chatbot
in the team. `OAuth2Application.allowed_chatbots` narrows that to the chatbots the application was
authorised for; an empty list authorises nothing.

Only client-credentials callers are gated. API-key, Django-session and authorization-code callers keep
team-membership semantics untouched.
"""

import json
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession, ParticipantData
from apps.teams.backends import CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    """A chatbot with an email channel, so the trigger-bot endpoints have a channel to dispatch on."""
    create_default_groups()
    experiment = ExperimentFactory.create(team=TeamFactory.create())
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
    )
    return experiment


def _machine_client(team, allowed_chatbots=None):
    """A machine (client-credentials) client holding chatbots:interact.

    The app owner is deliberately a non-member of the team: access must not fall back to a membership
    row or a role.
    """
    return ApiTestClient(
        UserFactory.create(),
        team,
        auth_method="oauth_client_credentials",
        scopes=["chatbots:interact"],
        allowed_chatbots=allowed_chatbots,
    )


def _post_chat_completions(client, experiment, public_id=None):
    url = reverse("api:openai-chat-completions", kwargs={"experiment_id": public_id or experiment.public_id})
    return client.post(url, data={"messages": [{"role": "user", "content": "hi"}], "user": "p1"}, format="json")


def _post_chat_completions_versioned(client, experiment, public_id=None):
    url = reverse(
        "api:openai-chat-completions-versioned",
        kwargs={"experiment_id": public_id or experiment.public_id, "version": 1},
    )
    return client.post(url, data={"messages": [{"role": "user", "content": "hi"}], "user": "p1"}, format="json")


def _post_api_message(client, experiment, public_id=None):
    url = reverse("channels:new_api_message", kwargs={"experiment_id": public_id or experiment.public_id})
    return client.post(url, data={"message": "hi"}, format="json")


def _post_api_message_versioned(client, experiment, public_id=None):
    url = reverse(
        "channels:new_api_message_versioned",
        kwargs={"experiment_id": public_id or experiment.public_id, "version": 1},
    )
    return client.post(url, data={"message": "hi"}, format="json")


def _trigger_bot_data(experiment, public_id=None):
    return {
        "identifier": "participant@example.com",
        "platform": ChannelPlatform.EMAIL,
        "experiment": str(public_id or experiment.public_id),
        "message_text": "hello",
    }


def _post_trigger_bot(client, experiment, public_id=None):
    return client.post(
        reverse("api:trigger_bot"),
        json.dumps(_trigger_bot_data(experiment, public_id)),
        content_type="application/json",
    )


def _post_trigger_bot_v2(client, experiment, public_id=None):
    return client.post(
        reverse("api:v2:trigger_bot"),
        json.dumps(_trigger_bot_data(experiment, public_id)),
        content_type="application/json",
    )


# Every view guarded by chatbots:interact.
ALL_ENDPOINTS = [
    pytest.param(_post_chat_completions, id="chat-completions"),
    pytest.param(_post_chat_completions_versioned, id="chat-completions-versioned"),
    pytest.param(_post_api_message, id="api-message"),
    pytest.param(_post_api_message_versioned, id="api-message-versioned"),
    pytest.param(_post_trigger_bot, id="trigger-bot-v1"),
    pytest.param(_post_trigger_bot_v2, id="trigger-bot-v2"),
]

# The subset a machine token can actually complete. `new_api_message` derives the participant from
# `request.user.email`, which a machine token (AnonymousUser) has no value for, so it has no success
# path to assert -- only the denial, which fires before it gets that far.
COMPLETABLE_ENDPOINTS = [
    pytest.param(_post_chat_completions, id="chat-completions"),
    pytest.param(_post_chat_completions_versioned, id="chat-completions-versioned"),
    pytest.param(_post_trigger_bot, id="trigger-bot-v1"),
    pytest.param(_post_trigger_bot_v2, id="trigger-bot-v2"),
]

# The subset that produces a response at all when addressed by a version's own public_id. The
# unversioned chat and message endpoints hand the snapshot straight to the version resolver, which
# rejects a non-family-head -- pre-existing behaviour that has nothing to do with the allowlist.
VERSION_ADDRESSABLE_ENDPOINTS = [
    pytest.param(_post_chat_completions_versioned, id="chat-completions-versioned"),
    pytest.param(_post_trigger_bot, id="trigger-bot-v1"),
    pytest.param(_post_trigger_bot_v2, id="trigger-bot-v2"),
]


@pytest.mark.django_db()
@pytest.mark.parametrize("post", ALL_ENDPOINTS)
def test_denied_when_chatbot_not_listed(post, experiment):
    """The application is authorised for a different chatbot in the same team."""
    other = ExperimentFactory.create(team=experiment.team)
    response = post(_machine_client(experiment.team, allowed_chatbots=[other]), experiment)
    assert response.status_code == 403, response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("post", ALL_ENDPOINTS)
def test_denied_when_allowlist_empty(post, experiment):
    """Empty means none: an application authorises nothing until someone says so."""
    response = post(_machine_client(experiment.team), experiment)
    assert response.status_code == 403, response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("post", COMPLETABLE_ENDPOINTS)
@patch("apps.api.views.channels.trigger_bot_message_task")
@patch("apps.api.openai.handle_api_message")
def test_allowed_when_chatbot_listed(handle_api_message, _trigger_task, post, experiment):
    handle_api_message.return_value = ChatMessage(content="ok")
    response = post(_machine_client(experiment.team, allowed_chatbots=[experiment]), experiment)
    assert response.status_code == 200, response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("post", VERSION_ADDRESSABLE_ENDPOINTS)
@patch("apps.api.views.channels.trigger_bot_message_task")
@patch("apps.api.openai.handle_api_message")
def test_version_is_normalised_to_the_working_version(handle_api_message, _trigger_task, post, experiment):
    """`create_new_version` assigns a fresh public_id, so a caller can address a version directly.

    The allowlist holds family heads, so addressing a version of a listed chatbot must not be denied.
    Only the gate is asserted: what these endpoints go on to do with a version-addressed request
    differs per endpoint and is not what this test is about.
    """
    handle_api_message.return_value = ChatMessage(content="ok")
    version = experiment.create_new_version()
    assert version.public_id != experiment.public_id

    client = _machine_client(experiment.team, allowed_chatbots=[experiment])
    response = post(client, experiment, public_id=version.public_id)

    assert response.status_code != 403, response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("post", ALL_ENDPOINTS)
def test_cross_team_chatbot_is_unreachable(post, experiment):
    """Listing a chatbot from another team does not widen the token past its pinned team."""
    victim = ExperimentFactory.create(team=TeamFactory.create())
    client = _machine_client(experiment.team, allowed_chatbots=[victim])
    response = post(client, victim)
    assert response.status_code in (403, 404), response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
@pytest.mark.parametrize("post", COMPLETABLE_ENDPOINTS)
@patch("apps.api.views.channels.trigger_bot_message_task")
@patch("apps.api.openai.handle_api_message")
def test_non_machine_callers_are_unaffected(handle_api_message, _trigger_task, post, auth_method, experiment):
    """API-key and authorization-code callers reach a chatbot no application lists."""
    handle_api_message.return_value = ChatMessage(content="ok")
    user = UserFactory.create()
    add_user_to_team(experiment.team, user, groups=[CHATBOT_ADMIN_GROUP])
    client = ApiTestClient(user, experiment.team, auth_method=auth_method)

    response = post(client, experiment)

    assert response.status_code == 200, response.content


@pytest.mark.django_db()
@patch("apps.api.views.channels.trigger_bot_message_task")
def test_denied_trigger_bot_creates_no_participant_data(trigger_bot_message_task, experiment):
    """The check runs before `prepare_trigger_bot_message`, which creates data as a side effect."""
    response = _post_trigger_bot(_machine_client(experiment.team), experiment)

    assert response.status_code == 403, response.content
    assert not ParticipantData.objects.filter(experiment=experiment).exists()
    trigger_bot_message_task.delay_on_commit.assert_not_called()


@pytest.mark.django_db()
def test_unknown_chatbot_is_a_404(experiment):
    """Resolving the chatbot up front means an unknown id reports as a 404, not a validation error."""
    client = _machine_client(experiment.team, allowed_chatbots=[experiment])
    response = _post_chat_completions(client, experiment, public_id=uuid.uuid4())

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
def test_denied_chat_completion_creates_no_session(experiment):
    """The check runs before `serializer.save()`, which would create a session and then reject it."""
    response = _post_chat_completions(_machine_client(experiment.team), experiment)

    assert response.status_code == 403, response.content
    assert not ExperimentSession.objects.filter(experiment=experiment).exists()
