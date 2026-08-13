"""Authorization plumbing for the v2 write API (#4139).

`ChatbotCompositionPermission` and `ChatbotWriteMixin` are shipped ahead of the sub-resource views
that will use them (#4140-#4145), so they are exercised directly here.
"""

import pytest
from django.http import Http404

from apps.api.permissions import ReadOnlyAPIKeyPermission
from apps.api.v2.write.base import ChatbotCompositionPermission, ChatbotWriteMixin
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ChatbotFactory, ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient


class _FakeRequest:
    def __init__(self, user, team, method="DELETE"):
        self.user = user
        self.team = team
        self.method = method
        self.auth = object()  # not an OAuth2AccessToken, so not a machine token


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
