import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.pipelines.models import Node
from apps.teams.backends import get_team_owner_groups
from apps.utils.factories.events import StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory, UserFactory


def _unarchive_url(team, experiment, version_number):
    return reverse("experiments:unarchive-experiment", args=[team.slug, experiment.id, version_number])


@pytest.fixture()
def archived_version(team_with_users):
    """A published version that has been archived, with its own static trigger."""
    owner = team_with_users.members.first()
    experiment = ExperimentFactory.create(team=team_with_users, owner=owner)
    version = experiment.create_new_version()
    version.archive()
    version.refresh_from_db()
    return experiment, version


@pytest.mark.django_db()
class TestUnarchiveVersion:
    def test_restores_the_version(self, client, team_with_users, archived_version):
        experiment, version = archived_version
        client.force_login(team_with_users.members.first())

        response = client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        assert response.status_code == 302
        version.refresh_from_db()
        assert version.is_archived is False

    def test_restores_the_linked_pipeline(self, client, team_with_users, archived_version):
        """A version's pipeline is archived alongside it. Restoring one restores the other."""
        experiment, version = archived_version
        assert version.pipeline.is_archived is True
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        version.pipeline.refresh_from_db()
        assert version.pipeline.is_archived is False

    def test_restores_the_pipeline_nodes(self, client, team_with_users, archived_version):
        """Pipeline.archive() reaches every node, not just the pipeline row."""
        experiment, version = archived_version
        nodes = Node.objects.get_all().filter(pipeline=version.pipeline)
        assert nodes.filter(is_archived=True).exists()
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        assert not nodes.filter(is_archived=True).exists()

    def test_restores_the_static_triggers(self, client, team_with_users):
        """Static triggers are archived with the version and must be restored with it."""
        owner = team_with_users.members.first()
        experiment = ExperimentFactory.create(team=team_with_users, owner=owner)
        version = experiment.create_new_version()
        trigger = StaticTriggerFactory.create(experiment=version)
        version.archive()
        trigger.refresh_from_db()
        assert trigger.is_archived is True
        client.force_login(owner)

        client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        trigger.refresh_from_db()
        assert trigger.is_archived is False

    def test_leaves_the_content_untouched(self, client, team_with_users, archived_version):
        experiment, version = archived_version
        before = (version.name, version.version_description, version.version_number)
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        version.refresh_from_db()
        assert (version.name, version.version_description, version.version_number) == before

    def test_get_is_rejected(self, client, team_with_users, archived_version):
        experiment, version = archived_version
        client.force_login(team_with_users.members.first())

        response = client.get(_unarchive_url(team_with_users, experiment, version.version_number))

        assert response.status_code == 405

    def test_details_modal_offers_the_action(self, client, team_with_users, archived_version):
        """The modal is the only route to unarchiving. Without the button there is no feature."""
        experiment, version = archived_version
        user = team_with_users.members.first()
        user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(user)
        url = reverse("chatbots:version-details", args=[team_with_users.slug, experiment.id, version.version_number])

        response = client.get(url)

        assert _unarchive_url(team_with_users, experiment, version.version_number) in response.content.decode()

    def test_active_version_is_left_alone(self, client, team_with_users):
        """Unarchiving an active version would reactivate records archived for other reasons."""
        owner = team_with_users.members.first()
        experiment = ExperimentFactory.create(team=team_with_users, owner=owner)
        version = experiment.create_new_version()
        trigger = StaticTriggerFactory.create(experiment=version)
        trigger.archive()
        client.force_login(owner)

        response = client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        assert response.status_code == 404
        trigger.refresh_from_db()
        assert trigger.is_archived is True

    def test_other_team_cannot_unarchive(self, client, team_with_users, archived_version):
        experiment, version = archived_version
        outsider = UserFactory()
        other_team = TeamFactory()
        # An admin of their own team. What stops them here is team scoping, not permissions.
        MembershipFactory.create(user=outsider, team=other_team, groups=get_team_owner_groups)
        client.force_login(outsider)

        response = client.post(_unarchive_url(other_team, experiment, version.version_number))

        assert response.status_code == 404
        version.refresh_from_db()
        assert version.is_archived is True

    def test_requires_change_permission(self, client, team_with_users, archived_version):
        """Being able to view a version is not licence to restore it."""
        experiment, version = archived_version
        viewer = UserFactory()
        MembershipFactory.create(user=viewer, team=team_with_users)
        viewer.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(viewer)

        response = client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        assert response.status_code == 403
        version.refresh_from_db()
        assert version.is_archived is True

    def test_version_of_an_archived_chatbot_stays_archived(self, client, team_with_users, archived_version):
        """Archiving a chatbot archives its versions; restoring one on its own would strand it."""
        experiment, version = archived_version
        experiment.archive()
        client.force_login(team_with_users.members.first())

        response = client.post(_unarchive_url(team_with_users, experiment, version.version_number))

        assert response.status_code == 403
        version.refresh_from_db()
        assert version.is_archived is True

    def test_details_modal_hides_the_action_for_an_archived_chatbot(self, client, team_with_users, archived_version):
        experiment, version = archived_version
        experiment.archive()
        user = team_with_users.members.first()
        user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(user)
        url = reverse("chatbots:version-details", args=[team_with_users.slug, experiment.id, version.version_number])

        response = client.get(url)

        assert _unarchive_url(team_with_users, experiment, version.version_number) not in response.content.decode()
