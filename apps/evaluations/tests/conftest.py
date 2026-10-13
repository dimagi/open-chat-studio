import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from apps.evaluations.models import Evaluator
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import GroupFactory


@pytest.fixture()
def evaluator_member(team_with_users):
    """Build a team member whose only evaluator permissions are the given codenames."""

    def _make(*codenames):
        content_type = ContentType.objects.get_for_model(Evaluator)
        group = GroupFactory.create(name="evaluator-" + "-".join(codenames))
        group.permissions.add(*Permission.objects.filter(content_type=content_type, codename__in=codenames))
        return MembershipFactory.create(team=team_with_users, groups=[group])

    return _make
