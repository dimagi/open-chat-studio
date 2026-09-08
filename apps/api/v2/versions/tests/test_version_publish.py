"""PATCH /api/v2/chatbots/{id}/versions/{version_number}/ -- serve an existing version (#4142).

The other half of choosing what participants get. `chatbot_version_create`'s `make_default` covers
a snapshot that goes live as it is taken; this covers a version that already exists, and creates
nothing -- so it is never refused for having no changes to publish.
"""

import pytest
from django.urls import reverse

from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

from .conftest import version_url


@pytest.fixture()
def published(chatbot):
    """Two versions, the first of them the one served.

    `create_new_version()` makes v1 the published version automatically, so v2 arrives unpublished
    and there is something for a promotion to do.
    """
    chatbot.create_new_version()
    chatbot.create_new_version()
    return chatbot


def promote(client, chatbot, version_number, **body):
    return client.patch(version_url(chatbot, version_number), body or {"is_published_version": True}, format="json")


def served(chatbot):
    """(version_number, published?) for every live version, oldest first.

    `is_published_version` is the API's name for the column the model calls `is_default_version`.
    """
    return [(v.version_number, v.is_default_version) for v in chatbot.versions.order_by("version_number")]


@pytest.mark.django_db()
def test_the_version_url_is_the_registered_route(chatbot):
    assert reverse("api:v2:chatbot-version", args=[chatbot.public_id, 2]) == version_url(chatbot, 2)


@pytest.mark.django_db()
class TestPromote:
    def test_promoting_a_version_serves_it(self, client, published):
        response = promote(client, published, 2)

        assert response.status_code == 200, response.content
        assert response.json() == {"version_number": 2, "is_published_version": True}

    def test_the_version_that_held_it_is_demoted(self, published, client):
        """A chatbot holds exactly one published version -- a partial unique constraint enforces it
        -- so promoting one has to take the flag off the incumbent in the same transaction."""
        assert promote(client, published, 2).status_code == 200

        assert served(published) == [(1, False), (2, True)]

    def test_promoting_the_version_already_served_is_not_an_error(self, client, published):
        """The request asked for a state that already holds, so it is answered rather than refused
        -- which also makes a retry after an answer the client never saw safe."""
        assert promote(client, published, 1).status_code == 200

        assert served(published) == [(1, True), (2, False)]

    def test_nothing_is_snapshotted(self, client, published):
        """The distinction from `make_default` on a publish: this creates no version, so it is not
        held to the no-changes rule that would refuse an identical snapshot."""
        assert promote(client, published, 2).status_code == 200

        assert published.versions.count() == 2
        published.refresh_from_db()
        assert not published.version_operation_in_progress


@pytest.mark.django_db()
class TestRefusals:
    def test_un_publishing_is_refused(self, client, published):
        """A chatbot always needs a published version, so there is no way to leave it with none.
        Moving it off a version means naming the version that takes over."""
        response = promote(client, published, 1, is_published_version=False)

        assert response.status_code == 400, response.content
        assert "is_published_version" in response.json()
        assert served(published) == [(1, True), (2, False)]

    def test_an_empty_body_is_refused(self, client, published):
        """`is_published_version` is required rather than defaulted: a PATCH is a statement of what
        to change, and an empty one asking to serve a different version is a client bug."""
        response = client.patch(version_url(published, 2), {}, format="json")

        assert response.status_code == 400, response.content
        assert "is_published_version" in response.json()

    def test_an_unrecognised_key_is_a_400_naming_it(self, client, published):
        response = client.patch(version_url(published, 2), {"is_defualt": True}, format="json")

        assert response.status_code == 400
        assert "is_defualt" in response.json()
        assert served(published) == [(1, True), (2, False)]

    def test_a_version_number_that_does_not_exist_is_a_404(self, client, published):
        assert promote(client, published, 99).status_code == 404

    def test_an_archived_version_is_a_404(self, client, published):
        """`versions` excludes archived rows, so a version a person tidied away cannot be served
        again through this API -- they restore it in the web app first."""
        published.versions.get(version_number=2).archive()

        assert promote(client, published, 2).status_code == 404

    def test_a_version_of_an_archived_chatbot_is_a_404(self, client, published):
        published.archive()

        assert promote(client, published, 1).status_code == 404

    def test_another_chatbots_version_number_is_a_404(self, client, published, team):
        other = ChatbotFactory.create(team=team, name="Other bot")
        other.create_new_version()

        assert promote(client, other, 2).status_code == 404


@pytest.mark.django_db()
class TestAuth:
    def test_another_teams_chatbot_is_a_404(self, published):
        other = TeamWithUsersFactory.create()

        response = promote(ApiTestClient(other.members.first(), other), published, 2)

        assert response.status_code == 404, response.content

    def test_a_read_only_key_cannot_promote(self, published):
        client = ApiTestClient(published.team.members.first(), published.team, read_only=True)

        assert promote(client, published, 2).status_code == 403
        assert served(published) == [(1, True), (2, False)]

    def test_a_token_without_the_write_scope_is_refused(self, published):
        client = ApiTestClient(
            published.team.members.first(), published.team, auth_method="oauth", scopes=["chatbots:read"]
        )

        assert promote(client, published, 2).status_code == 403

    def test_a_machine_token_is_held_to_its_applications_chatbot_allowlist(self, published):
        client = ApiTestClient(
            published.team.members.first(),
            published.team,
            auth_method="oauth_client_credentials",
            scopes=["chatbots:write"],
            allowed_chatbots=[],
        )

        assert promote(client, published, 2).status_code == 403


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
        pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-promote"),
        pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
    ],
)
def test_promoting_requires_change_experiment(team_with_roles, group, allowed):
    """`change_experiment`, not the `delete_experiment` its DELETE sibling asks for: choosing which
    version is served changes the chatbot, and a role that may edit chatbots but not delete them
    should be able to do it."""
    chatbot = ChatbotFactory.create(team=team_with_roles)
    chatbot.create_new_version()
    chatbot.create_new_version()
    user = UserFactory.create()
    add_user_to_team(team_with_roles, user, [group])

    response = promote(ApiTestClient(user, team_with_roles), chatbot, 2)

    assert response.status_code == (200 if allowed else 403), response.content
