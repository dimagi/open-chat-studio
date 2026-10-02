"""Authorization for the content resource endpoints (#4145): role permissions and OAuth scopes."""

import pytest
from django.urls import get_resolver

from apps.api.v2.urls import router
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP
from apps.utils.tests.clients import ApiTestClient

from .conftest import RESOURCES, client_for_role

ROLE_CASES = [
    pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-write"),
    pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
]


@pytest.mark.django_db()
@pytest.mark.parametrize("resource", RESOURCES)
@pytest.mark.parametrize(("group", "allowed"), ROLE_CASES)
class TestRoles:
    """Each verb needs the model's own add/change/delete permission, as in the web app."""

    def test_create(self, team_with_roles, group, allowed, resource):
        response = client_for_role(team_with_roles, group).post(resource.list_url, resource.create_body, format="json")

        assert response.status_code == (201 if allowed else 403), response.content

    @pytest.mark.parametrize("method", ["patch", "delete"])
    def test_change(self, team_with_roles, group, allowed, resource, method):
        row = resource.factory.create(team=team_with_roles)

        response = getattr(client_for_role(team_with_roles, group), method)(
            resource.detail_url(row.id), {resource.patch_field: "X"}, format="json"
        )

        assert response.status_code == (200 if allowed else 403), response.content


@pytest.mark.django_db()
@pytest.mark.parametrize("resource", RESOURCES)
class TestOAuthScopes:
    """Writes need `chatbots:write`, reads `chatbots:read`: the content is part of a chatbot's composition."""

    def _client(self, team, scopes):
        return ApiTestClient(team.members.first(), team, auth_method="oauth", scopes=scopes)

    def test_read_scope_may_read(self, team, resource):
        resource.factory.create(team=team)

        assert self._client(team, ["chatbots:read"]).get(resource.list_url).status_code == 200

    @pytest.mark.parametrize(
        ("scope", "expected_status"),
        [
            pytest.param("chatbots:read", 403, id="read-may-not-write"),
            pytest.param("chatbots:interact", 403, id="interact-may-not-write"),
            pytest.param("chatbots:write", 201, id="write-may-write"),
        ],
    )
    def test_write(self, team, resource, scope, expected_status):
        response = self._client(team, [scope]).post(resource.list_url, resource.create_body, format="json")

        assert response.status_code == expected_status, response.content


def test_there_is_no_provider_endpoint():
    """Provider credentials hold secrets, so the API never accepts them (spec W4). Providers are
    referenced by the ids the options endpoints list, and never written."""
    routes = [str(pattern.pattern) for pattern in get_resolver("apps.api.v2.urls").url_patterns]
    routes += [str(pattern.pattern) for pattern in router.urls]

    assert not [route for route in routes if "provider" in route]


@pytest.mark.django_db()
@pytest.mark.parametrize("resource", RESOURCES)
class TestMachineTokens:
    """A machine token may read content but not write it: content is shared by chatbots outside its allowlist."""

    def _client(self, team):
        return ApiTestClient(
            team.members.first(),
            team,
            auth_method="oauth_client_credentials",
            scopes=["chatbots:read", "chatbots:write"],
        )

    def test_may_read(self, team, resource):
        row = resource.factory.create(team=team)

        assert self._client(team).get(resource.detail_url(row.id)).status_code == 200

    def test_may_not_create(self, team, resource):
        client = self._client(team)
        count_before = resource.model.objects.filter(team=team).count()

        response = client.post(resource.list_url, resource.create_body, format="json")

        assert response.status_code == 403, response.content
        assert resource.model.objects.filter(team=team).count() == count_before

    @pytest.mark.parametrize("method", ["patch", "delete"])
    def test_may_not_change(self, team, resource, method):
        row = resource.factory.create(team=team, **{resource.patch_field: "Before"})

        client = self._client(team)

        response = getattr(client, method)(resource.detail_url(row.id), {resource.patch_field: "X"}, format="json")

        assert response.status_code == 403, response.content
        row.refresh_from_db()
        assert getattr(row, resource.patch_field) == "Before"
        assert row.is_archived is False
