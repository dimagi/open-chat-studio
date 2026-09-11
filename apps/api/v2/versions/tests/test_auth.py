"""Authorization for publishing a chatbot version (#4142).

Publishing is a *change* to the chatbot rather than a new resource of its own, so the gate is
`change_experiment` and not `add_experiment` -- the permission the pipeline façade asks for, since
publishing is the last step of the same build.
"""

import pytest

from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

from .conftest import versions_url


@pytest.fixture()
def team_with_roles(db):
    """`create_default_groups()` is explicit so the DB-backed groups match backends.py even
    though pytest runs with --reuse-db."""
    create_default_groups()
    team = TeamFactory.create()
    token = set_current_team(team)
    try:
        yield team
    finally:
        unset_current_team(token)


def publish(client, chatbot):
    return client.post(versions_url(chatbot), {"make_default": True}, format="json")


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("group", "allowed"),
    [
        pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-publish"),
        pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
    ],
)
def test_publishing_requires_change_experiment(team_with_roles, dispatch, group, allowed):
    chatbot = ChatbotFactory.create(team=team_with_roles)
    user = UserFactory.create()
    add_user_to_team(team_with_roles, user, [group])

    response = publish(ApiTestClient(user, team_with_roles), chatbot)

    assert response.status_code == (202 if allowed else 403), response.content


@pytest.mark.django_db()
def test_a_read_only_key_cannot_publish(chatbot, dispatch):
    """`UserAPIKey.read_only` defaults to True, so publishing takes a key an operator issued
    deliberately."""
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, read_only=True)

    assert publish(client, chatbot).status_code == 403
    dispatch.assert_not_called()


@pytest.mark.django_db()
def test_a_token_without_the_write_scope_is_refused(chatbot, dispatch):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, auth_method="oauth", scopes=["chatbots:read"])

    assert publish(client, chatbot).status_code == 403
    dispatch.assert_not_called()


@pytest.mark.django_db()
def test_another_teams_chatbot_is_not_found(chatbot, dispatch):
    other = TeamWithUsersFactory.create()

    response = publish(ApiTestClient(other.members.first(), other), chatbot)

    assert response.status_code == 404, response.content
    dispatch.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "listed",
    [
        pytest.param(False, id="unlisted-is-refused"),
        pytest.param(True, id="listed-may-publish"),
    ],
)
def test_a_machine_token_is_held_to_its_applications_chatbot_allowlist(chatbot, dispatch, listed):
    client = ApiTestClient(
        UserFactory.create(),
        chatbot.team,
        auth_method="oauth_client_credentials",
        scopes=["chatbots:write"],
        allowed_chatbots=[chatbot] if listed else [],
    )

    response = publish(client, chatbot)

    assert response.status_code == (202 if listed else 403), response.content
