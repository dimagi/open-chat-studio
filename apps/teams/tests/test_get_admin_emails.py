import pytest
from django.contrib.auth.models import Group

from apps.teams.backends import TEAM_ADMIN_GROUP
from apps.teams.utils import set_current_team
from apps.users.models import CustomUser
from apps.utils.deletion import get_admin_emails_with_delete_permission
from apps.utils.factories.team import MembershipFactory


@pytest.mark.django_db()
def test_get_admin_emails_can_delete_team(team_with_users):
    set_current_team(team_with_users)

    MembershipFactory(team=team_with_users, groups=lambda: list(Group.objects.filter(name=TEAM_ADMIN_GROUP)))

    expected = {
        user.email
        for user in CustomUser.objects.filter(membership__team=team_with_users).all()
        if user.has_perm("teams.delete_team")
    }
    emails = get_admin_emails_with_delete_permission(team_with_users)
    assert set(emails) == expected
