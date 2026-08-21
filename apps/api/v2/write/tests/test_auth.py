"""Authorization plumbing for the v2 write API (#4139).

`ChatbotCompositionPermission` is shipped ahead of the sub-resource views that will use it
(#4140-#4145), so it is exercised directly here.
"""

import pytest

from apps.api.v2.write.base import ChatbotCompositionPermission
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient


class _FakeRequest:
    def __init__(self, user, team, method="DELETE"):
        self.user = user
        self.team = team
        self.method = method
        # Deliberately not an OAuth2AccessToken, so the request is not a machine token.
        self.auth = object()


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


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("group", "allowed"),
    [
        pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-edit-composition"),
        pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
    ],
)
def test_composition_permission_reads_change_experiment_whatever_the_verb(team_with_roles, group, allowed):
    """Deleting a pipeline node is a *change* to the chatbot. The stock DjangoModelPermissions
    verb map would demand delete_experiment for this DELETE; this class must not."""
    user = UserFactory.create()
    add_user_to_team(team_with_roles, user, [group])
    request = _FakeRequest(user, team_with_roles, method="DELETE")
    assert ChatbotCompositionPermission().has_permission(request, view=None) is allowed


# The two shipped endpoints are gated by `DjangoModelPermissionsWithView` on `ChatbotViewSet`, not
# by `ChatbotCompositionPermission` above -- nothing uses that class yet. Team membership alone must
# not be enough to write: the caller's role has to hold the same model permissions the chatbot UI
# requires. Chat Viewer holds neither add_experiment nor change_experiment; Chatbot Admin holds both.
ROLE_CASES = [
    pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-write"),
    pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
]


def _client_for_role(team, group):
    """An API client for a new team member holding only `group`."""
    user = UserFactory.create()
    add_user_to_team(team, user, [group])
    return ApiTestClient(user, team)


@pytest.mark.django_db()
@pytest.mark.parametrize(("group", "allowed"), ROLE_CASES)
def test_create_requires_add_experiment(team_with_roles, group, allowed):
    response = _client_for_role(team_with_roles, group).post("/api/v2/chatbots/", {"name": "Role gated"}, format="json")

    assert response.status_code == (201 if allowed else 403), response.content


@pytest.mark.django_db()
@pytest.mark.parametrize(("group", "allowed"), ROLE_CASES)
def test_patch_requires_change_experiment(team_with_roles, group, allowed):
    chatbot = ChatbotFactory.create(team=team_with_roles)
    response = _client_for_role(team_with_roles, group).patch(
        f"/api/v2/chatbots/{chatbot.public_id}/", {"name": "Role gated"}, format="json"
    )

    assert response.status_code == (200 if allowed else 403), response.content


def _machine_write_client(team, allowed_chatbots=None):
    """A machine (client-credentials) client holding chatbots:write.

    The app owner is deliberately a non-member of the team, matching
    `apps/api/tests/test_application_chatbot_allowlist.py`: authorization must rest on the scope and
    the allowlist, never on a membership row.
    """
    return ApiTestClient(
        UserFactory.create(),
        team,
        auth_method="oauth_client_credentials",
        scopes=["chatbots:write"],
        allowed_chatbots=allowed_chatbots,
    )


# The allowed case is `test_machine_token_can_patch` in test_chatbot_patch.py; only the refusals
# live here. `application_allows_chatbot` filters on the chatbot's pk, so an empty allowlist and one
# naming a different chatbot are the same code path -- one refusal case covers both.
@pytest.mark.django_db()
def test_patch_refuses_an_unlisted_chatbot(team_with_roles):
    chatbot = ChatbotFactory.create(team=team_with_roles)
    client = _machine_write_client(team_with_roles, allowed_chatbots=[])

    response = client.patch(f"/api/v2/chatbots/{chatbot.public_id}/", {"name": "Nope"}, format="json")

    assert response.status_code == 403, response.content
    chatbot.refresh_from_db()
    assert chatbot.name != "Nope"


@pytest.mark.django_db()
def test_create_is_not_gated_by_the_allowlist(team_with_roles):
    """A new chatbot has no id yet, so it cannot be on any allowlist. Gating POST would leave a
    machine token unable to create one at all -- an asymmetry that reads as an oversight without
    this test to say it was deliberate."""
    response = _machine_write_client(team_with_roles, allowed_chatbots=[]).post(
        "/api/v2/chatbots/", {"name": "Bootstrapped"}, format="json"
    )

    assert response.status_code == 201, response.content
