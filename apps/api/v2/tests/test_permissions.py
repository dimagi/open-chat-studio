"""`ApplicationAllowsChatbot` admits every credential except a limited machine token on an unlisted chatbot."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.api.v2.permissions import ApplicationAllowsChatbot
from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamWithUsersFactory


def _machine_token(team, *, allowed_chatbots=(), all_data=False):
    application = OAuth2Application.objects.create(
        name="machine-app",
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
        all_data=all_data,
    )
    application.allowed_chatbots.set(allowed_chatbots)
    return OAuth2AccessToken.objects.create(
        application=application, team=team, token="machine-token", scope="chatbots:write", expires=_tomorrow()
    )


def _authorization_code_token(team):
    return OAuth2AccessToken.objects.create(
        user=team.members.first(), team=team, token="user-token", scope="chatbots:write", expires=_tomorrow()
    )


def _tomorrow():
    return timezone.now() + timedelta(days=1)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("credential", "allowed"),
    [
        pytest.param(lambda team, chatbot: object(), True, id="api-key-or-session"),
        pytest.param(lambda team, chatbot: _authorization_code_token(team), True, id="authorization-code-token"),
        pytest.param(lambda team, chatbot: _machine_token(team), False, id="machine-unlisted"),
        pytest.param(lambda team, chatbot: _machine_token(team, allowed_chatbots=[chatbot]), True, id="machine-listed"),
        pytest.param(lambda team, chatbot: _machine_token(team, all_data=True), True, id="machine-all-data"),
    ],
)
def test_admits_every_credential_but_a_limited_machine_token_on_an_unlisted_chatbot(credential, allowed):
    """Only a machine token whose application neither lists the chatbot nor holds all_data is refused."""
    team = TeamWithUsersFactory.create()
    chatbot = ChatbotFactory.create(team=team)
    request = SimpleNamespace(auth=credential(team, chatbot), method="PATCH")

    assert ApplicationAllowsChatbot().has_object_permission(request, None, chatbot) is allowed


@pytest.mark.django_db()
def test_a_listed_chatbot_addressed_by_one_of_its_versions_is_admitted():
    """The allowlist holds family heads; a caller may name a version by its own public_id."""
    team = TeamWithUsersFactory.create()
    chatbot = ChatbotFactory.create(team=team)
    version = chatbot.create_new_version()
    request = SimpleNamespace(auth=_machine_token(team, allowed_chatbots=[chatbot]), method="PATCH")

    assert ApplicationAllowsChatbot().has_object_permission(request, None, version) is True


def test_the_refusal_reads_the_same_whatever_the_verb():
    """The class carries no verb logic, so one message serves reads and writes; clients may match on it."""
    assert ApplicationAllowsChatbot.message == "This application is not authorized to access this chatbot."
