"""DELETE /api/v2/chatbots/{id}/versions/{version_number}/ -- archive one version (#4143).

Archiving a version is a soft delete of one snapshot, not a teardown of the chatbot. The channel
guard and the scheduled-message count the chatbot-level archive carries have nothing to do here:
channels and schedules hang off the working row, never off a snapshot.
"""

import pytest
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.teams.backends import CHAT_VIEWER_GROUP, CHATBOT_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

from .conftest import version_url


@pytest.fixture()
def published(chatbot):
    """Two versions, the second of them the default -- so there is a version each rule applies to."""
    chatbot.create_new_version()
    chatbot.create_new_version(make_default=True)
    return chatbot


def archive(client, chatbot, version_number):
    return client.delete(version_url(chatbot, version_number))


@pytest.mark.django_db()
def test_the_archive_url_is_the_registered_route(chatbot):
    assert reverse("api:v2:chatbot-version", args=[chatbot.public_id, 2]) == version_url(chatbot, 2)


@pytest.mark.django_db()
class TestArchive:
    def test_a_published_version_archives(self, client, published):
        response = archive(client, published, 1)

        assert response.status_code == 200, response.content
        assert response.json() == {"archived": True}
        assert Experiment.objects.get_all().get(working_version=published, version_number=1).is_archived is True

    def test_the_other_versions_and_the_working_row_are_left_alone(self, client, published):
        assert archive(client, published, 1).status_code == 200

        published.refresh_from_db()
        assert published.is_archived is False
        assert [version.version_number for version in published.versions.all()] == [2]

    def test_archiving_a_version_takes_its_pipeline_with_it(self, client, published):
        """A version owns its own pipeline snapshot, so the snapshot goes when the version does --
        the same as the web app's per-version archive. The working pipeline is untouched."""
        version = published.versions.get(version_number=1)
        version_pipeline_id = version.pipeline_id

        assert archive(client, published, 1).status_code == 200

        assert Pipeline.objects.get_all().get(id=version_pipeline_id).is_archived is True
        published.refresh_from_db()
        assert published.pipeline.is_archived is False

    def test_repeating_the_call_is_not_found(self, client, published):
        """Archived rows are outside the queryset this resolves through, so a retry after an answer
        the client never saw is safe rather than a second archive."""
        assert archive(client, published, 1).status_code == 200

        assert archive(client, published, 1).status_code == 404


@pytest.mark.django_db()
class TestRefusals:
    def test_the_default_version_is_refused(self, client, published):
        """It is the version participants are served, and a family may hold only one, so archiving
        it would leave the chatbot with none. The published version is moved first."""
        response = archive(client, published, 2)

        assert response.status_code == 409, response.content
        assert "published version" in response.json()["detail"]
        assert published.versions.get(version_number=2).is_archived is False

    def test_a_version_number_that_does_not_exist_is_not_found(self, client, published):
        assert archive(client, published, 99).status_code == 404

    def test_a_version_of_an_archived_chatbot_is_not_found(self, client, published):
        """The chatbot is resolved first and archived chatbots are outside every v2 write path, so
        this never reaches the version rule."""
        published.archive()

        assert archive(client, published, 1).status_code == 404

    def test_another_chatbots_version_number_is_not_found(self, client, published, team):
        """The version is looked for among this chatbot's own, so a number that exists elsewhere
        does not resolve here."""
        other = ChatbotFactory.create(team=team, name="Other bot")
        other.create_new_version()

        assert archive(client, other, 2).status_code == 404
        assert other.versions.get(version_number=1).is_archived is False


@pytest.mark.django_db()
class TestAuth:
    def test_another_teams_chatbot_is_not_found(self, published):
        other = TeamWithUsersFactory.create()

        response = archive(ApiTestClient(other.members.first(), other), published, 1)

        assert response.status_code == 404, response.content

    def test_a_read_only_key_cannot_archive(self, published):
        client = ApiTestClient(published.team.members.first(), published.team, read_only=True)

        assert archive(client, published, 1).status_code == 403
        assert published.versions.get(version_number=1).is_archived is False

    def test_a_token_without_the_write_scope_is_refused(self, published):
        client = ApiTestClient(
            published.team.members.first(), published.team, auth_method="oauth", scopes=["chatbots:read"]
        )

        assert archive(client, published, 1).status_code == 403

    @pytest.mark.parametrize(
        "listed",
        [
            pytest.param(False, id="unlisted-is-refused"),
            pytest.param(True, id="listed-may-archive"),
        ],
    )
    def test_a_machine_token_is_held_to_its_applications_chatbot_allowlist(self, published, listed):
        client = ApiTestClient(
            UserFactory.create(),
            published.team,
            auth_method="oauth_client_credentials",
            scopes=["chatbots:write"],
            allowed_chatbots=[published] if listed else [],
        )

        assert archive(client, published, 1).status_code == (200 if listed else 403)


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
        pytest.param(CHATBOT_ADMIN_GROUP, True, id="chatbot-admin-may-archive"),
        pytest.param(CHAT_VIEWER_GROUP, False, id="chat-viewer-may-not"),
    ],
)
def test_archiving_a_version_requires_delete_experiment(team_with_roles, group, allowed):
    """Unlike the rest of the chatbot sub-resources -- where the verb map would ask the wrong
    question -- this one really is a delete: a version is an `Experiment` row and this hides one."""
    chatbot = ChatbotFactory.create(team=team_with_roles)
    chatbot.create_new_version()
    # v1 is made the default automatically, so a second version has to take that over before the
    # first one is archivable at all.
    chatbot.create_new_version(make_default=True)
    user = UserFactory.create()
    add_user_to_team(team_with_roles, user, [group])

    response = archive(ApiTestClient(user, team_with_roles), chatbot, 1)

    assert response.status_code == (200 if allowed else 403), response.content
