"""Authorization for the pipeline façade (#4140).

Editing a chatbot's composition is a *change* to the chatbot whatever the verb, so all three node
endpoints demand the same permission — deleting a node is not deleting the chatbot.
"""

import pytest

from apps.pipelines.models import Node
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

from .conftest import node_url, nodes_url


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


def _call(client, chatbot, verb):
    """Exercise one verb against `chatbot`, with a node of its own to edit.

    Not the Start node: the endpoints refuse to edit or delete it whatever the caller's role, so it
    could not tell a permission failure from the endpoint's own answer.
    """
    if verb == "post":
        return client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")
    node = Node.objects.create(
        pipeline=chatbot.pipeline, flow_id="LLMResponseWithPrompt-auth1", type="LLMResponseWithPrompt", params={}
    )
    if verb == "patch":
        return client.patch(node_url(chatbot, node.flow_id), {"label": "Renamed"}, format="json")
    return client.delete(node_url(chatbot, node.flow_id))


ALLOWED_STATUS = {"post": 201, "patch": 200, "delete": 200}
VERBS = ["post", "patch", "delete"]


@pytest.mark.django_db()
@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize(
    ("group", "allowed"),
    [
        pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-edit"),
        pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
    ],
)
def test_every_verb_requires_change_experiment(team_with_roles, verb, group, allowed):
    """Team membership alone is not enough: the role has to hold the permission the builder
    requires. The stock verb->permission map would have demanded `delete_experiment` for DELETE."""
    chatbot = ChatbotFactory.create(team=team_with_roles)
    user = UserFactory.create()
    add_user_to_team(team_with_roles, user, [group])

    response = _call(ApiTestClient(user, team_with_roles), chatbot, verb)

    assert response.status_code == (ALLOWED_STATUS[verb] if allowed else 403), response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("verb", VERBS)
def test_a_read_only_key_cannot_edit(verb):
    """`UserAPIKey.read_only` defaults to True, so writing takes a key an operator issued
    deliberately."""
    chatbot = ChatbotFactory.create(team=TeamWithUsersFactory.create())
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, read_only=True)

    assert _call(client, chatbot, verb).status_code == 403


@pytest.mark.django_db()
@pytest.mark.parametrize("verb", VERBS)
def test_another_teams_chatbot_is_not_found(verb):
    chatbot = ChatbotFactory.create(team=TeamWithUsersFactory.create())
    other = TeamWithUsersFactory.create()

    response = _call(ApiTestClient(other.members.first(), other), chatbot, verb)

    assert response.status_code == 404, response.content


def _machine_client(team, allowed_chatbots):
    return ApiTestClient(
        UserFactory.create(),
        team,
        auth_method="oauth_client_credentials",
        scopes=["chatbots:write"],
        allowed_chatbots=allowed_chatbots,
    )


@pytest.mark.django_db()
@pytest.mark.parametrize("verb", VERBS)
def test_a_machine_token_is_refused_an_unlisted_chatbot(verb):
    """Unlike POST /chatbots/, which cannot be gated because the chatbot does not exist yet, every
    façade write names an existing chatbot and so is held to the application's allowlist."""
    chatbot = ChatbotFactory.create(team=TeamWithUsersFactory.create())

    response = _call(_machine_client(chatbot.team, allowed_chatbots=[]), chatbot, verb)

    assert response.status_code == 403, response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("verb", VERBS)
def test_a_machine_token_may_edit_a_listed_chatbot(verb):
    chatbot = ChatbotFactory.create(team=TeamWithUsersFactory.create())

    response = _call(_machine_client(chatbot.team, allowed_chatbots=[chatbot]), chatbot, verb)

    assert response.status_code == ALLOWED_STATUS[verb], response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("verb", VERBS)
def test_a_token_without_the_write_scope_is_refused(verb):
    chatbot = ChatbotFactory.create(team=TeamWithUsersFactory.create())
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, auth_method="oauth", scopes=["chatbots:read"])

    assert _call(client, chatbot, verb).status_code == 403
