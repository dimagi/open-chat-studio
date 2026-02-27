import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from apps.experiments.models import Experiment
from apps.teams.backends import TeamBackend
from apps.teams.utils import current_team
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import GroupFactory


@pytest.fixture()
def experiment_content_type():
    return ContentType.objects.get_for_model(Experiment)


@pytest.fixture()
def experiment_permissions(experiment_content_type):
    return {perm.codename: perm for perm in Permission.objects.filter(content_type=experiment_content_type)}


@pytest.fixture()
def group1(experiment_permissions):
    group = GroupFactory(name="group1")
    group.permissions.add(experiment_permissions["add_experiment"], experiment_permissions["change_experiment"])
    return group


@pytest.fixture()
def group2(experiment_permissions):
    group = GroupFactory(name="group2")
    group.permissions.add(experiment_permissions["view_experiment"], experiment_permissions["delete_experiment"])
    return group


@pytest.fixture(autouse=True)
def _use_team_backend(settings):
    settings.AUTHENTICATION_BACKENDS = ["apps.teams.backends.TeamBackend"]


@pytest.mark.django_db()
def test_team_backend_no_current_team(group1, group2):
    """Test that the backend returns no permissions (and doesn't error)
    if there is no team set in the 'current_team' context."""
    membership = MembershipFactory(groups=[group1, group2])
    user = membership.user
    assert TeamBackend().get_group_permissions(user) == set()  # ty: ignore[invalid-argument-type]


@pytest.mark.django_db()
def test_team_backend(group1, group2):
    membership = MembershipFactory(groups=[group1, group2])
    user = membership.user
    with current_team(membership.team):
        assert TeamBackend().get_group_permissions(user) == {  # ty: ignore[invalid-argument-type]
            "experiments.add_experiment",
            "experiments.change_experiment",
            "experiments.view_experiment",
            "experiments.delete_experiment",
        }


@pytest.mark.django_db()
def test_team_backend_user_permissions(group1):
    membership = MembershipFactory(groups=[group1])
    user = membership.user
    with current_team(membership.team):
        assert user.has_perm("experiments.add_experiment")
        assert not user.has_perm("experiments.view_experiment")
