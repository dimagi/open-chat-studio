"""Authorization plumbing for the v2 write API (#4139).

`ChatbotCompositionPermission` and `ChatbotWriteMixin` are shipped ahead of the sub-resource views
that will use them (#4140-#4145), so they are exercised directly here.
"""

import pytest
from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from apps.api.permissions import ReadOnlyAPIKeyPermission
from apps.api.v2.write.base import ChatbotCompositionPermission, ChatbotWriteMixin
from apps.oauth.models import OAuth2AccessToken
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ChatbotFactory, ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient


class _FakeRequest:
    def __init__(self, user, team, method="DELETE", auth=None):
        self.user = user
        self.team = team
        self.method = method
        # Default is deliberately not an OAuth2AccessToken, so the request is not a machine token.
        self.auth = auth if auth is not None else object()


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


def test_write_mixin_keeps_the_read_only_api_key_gate():
    """Overriding permission_classes replaces the project defaults, which is how the read-only
    API-key gate (ADR-0021) gets silently dropped."""
    assert ReadOnlyAPIKeyPermission in ChatbotWriteMixin.permission_classes
    assert ChatbotCompositionPermission in ChatbotWriteMixin.permission_classes


def test_write_mixin_declares_the_chatbots_scope():
    """TokenHasOAuthResourceScope turns this into chatbots:read on GET and chatbots:write on
    every write verb."""
    assert ChatbotWriteMixin.required_scopes == ["chatbots"]


class _View(ChatbotWriteMixin):
    def __init__(self, request, chatbot_id):
        self.request = request
        self.kwargs = {"id": str(chatbot_id)}


@pytest.mark.django_db()
def test_get_chatbot_returns_this_teams_working_chatbot():
    chatbot = ExperimentFactory.create()
    view = _View(_FakeRequest(UserFactory.create(), chatbot.team), chatbot.public_id)
    assert view.get_chatbot() == chatbot


@pytest.mark.django_db()
def test_get_chatbot_404s_on_another_teams_chatbot():
    chatbot = ExperimentFactory.create()
    view = _View(_FakeRequest(UserFactory.create(), TeamFactory.create()), chatbot.public_id)
    with pytest.raises(Http404):
        view.get_chatbot()


@pytest.mark.django_db()
def test_get_chatbot_404s_on_a_malformed_id():
    """`public_id` is a UUIDField, so a non-UUID raises ValidationError rather than missing the
    queryset. Every sub-resource in #4140-#4145 resolves its chatbot through here, so letting that
    escape would make one 500 per endpoint."""
    view = _View(_FakeRequest(UserFactory.create(), TeamFactory.create()), "not-a-uuid")
    with pytest.raises(Http404):
        view.get_chatbot()


@pytest.mark.django_db()
def test_get_chatbot_404s_on_a_version_snapshot():
    """Snapshots are immutable; writes only ever target the working version."""
    working = ExperimentFactory.create()
    snapshot = ExperimentFactory.create(team=working.team, working_version=working, version_number=1)
    view = _View(_FakeRequest(UserFactory.create(), working.team), snapshot.public_id)
    with pytest.raises(Http404):
        view.get_chatbot()


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


@pytest.mark.django_db()
def test_get_chatbot_enforces_the_allowlist_for_every_sub_resource(team_with_roles):
    """The gate lives in `get_chatbot` so the sub-resources in #4140-#4145 inherit it. Enforcing it
    per view instead would mean each new endpoint is one forgotten call away from an ungated write."""
    chatbot = ChatbotFactory.create(team=team_with_roles)
    # A real OAuth2AccessToken on a client-credentials application: `is_client_credentials_request`
    # inspects the token's grant type, so a stub would test the branch's existence, not the branch.
    client = _machine_write_client(team_with_roles, allowed_chatbots=[])
    token = OAuth2AccessToken.objects.get(application=client.application)
    view = _View(_FakeRequest(user=None, team=team_with_roles, method="PATCH", auth=token), chatbot.public_id)

    with pytest.raises(PermissionDenied):
        view.get_chatbot()
