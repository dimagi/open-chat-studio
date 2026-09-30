"""Authorization for the content resource endpoints (#4145): role permissions and OAuth scopes."""

import pytest
from django.urls import get_resolver

from apps.api.v2.urls import router
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ConsentFormFactory, SourceMaterialFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

RESOURCES = [
    pytest.param("source-material", SourceMaterialFactory, {"topic": "T", "material": "M"}, id="source-material"),
    pytest.param("consent-forms", ConsentFormFactory, {"name": "N", "consent_text": "C"}, id="consent-forms"),
]


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


def _client_for_role(team, group):
    user = UserFactory.create()
    add_user_to_team(team, user, [group])
    return ApiTestClient(user, team)


ROLE_CASES = [
    pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-write"),
    pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
]


@pytest.mark.django_db()
@pytest.mark.parametrize(("path", "factory", "body"), RESOURCES)
@pytest.mark.parametrize(("group", "allowed"), ROLE_CASES)
class TestRoles:
    """Each verb needs the model's own add/change/delete permission, as in the web app."""

    def test_create(self, team_with_roles, group, allowed, path, factory, body):
        response = _client_for_role(team_with_roles, group).post(f"/api/v2/{path}/", body, format="json")

        assert response.status_code == (201 if allowed else 403), response.content

    def test_patch(self, team_with_roles, group, allowed, path, factory, body):
        row = factory.create(team=team_with_roles)

        response = _client_for_role(team_with_roles, group).patch(f"/api/v2/{path}/{row.id}/", body, format="json")

        assert response.status_code == (200 if allowed else 403), response.content

    def test_archive(self, team_with_roles, group, allowed, path, factory, body):
        row = factory.create(team=team_with_roles)

        response = _client_for_role(team_with_roles, group).delete(f"/api/v2/{path}/{row.id}/")

        assert response.status_code == (200 if allowed else 403), response.content


@pytest.mark.django_db()
@pytest.mark.parametrize(("path", "factory", "body"), RESOURCES)
class TestOAuthScopes:
    """Writes need `chatbots:write`, reads `chatbots:read`: the content is part of a chatbot's composition."""

    @pytest.fixture()
    def team(self, db):
        return TeamWithUsersFactory.create()

    def _client(self, team, scopes):
        return ApiTestClient(team.members.first(), team, auth_method="oauth", scopes=scopes)

    def test_read_scope_may_read(self, team, path, factory, body):
        factory.create(team=team)

        assert self._client(team, ["chatbots:read"]).get(f"/api/v2/{path}/").status_code == 200

    def test_read_scope_may_not_write(self, team, path, factory, body):
        response = self._client(team, ["chatbots:read"]).post(f"/api/v2/{path}/", body, format="json")

        assert response.status_code == 403

    def test_interact_scope_may_not_write(self, team, path, factory, body):
        response = self._client(team, ["chatbots:interact"]).post(f"/api/v2/{path}/", body, format="json")

        assert response.status_code == 403

    def test_write_scope_may_write(self, team, path, factory, body):
        response = self._client(team, ["chatbots:write"]).post(f"/api/v2/{path}/", body, format="json")

        assert response.status_code == 201, response.content


def test_there_is_no_provider_endpoint():
    """Provider credentials hold secrets, so the API never accepts them (spec W4). Providers are
    referenced by the ids the options endpoints list, and never written."""
    routes = [str(pattern.pattern) for pattern in get_resolver("apps.api.v2.urls").url_patterns]
    routes += [str(pattern.pattern) for pattern in router.urls]

    assert not [route for route in routes if "provider" in route]
