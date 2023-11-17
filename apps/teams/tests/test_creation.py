from django.test import TestCase

from apps.teams.backends import SUPER_ADMIN_GROUP
from apps.teams.helpers import create_default_team_for_user
from apps.teams.roles import is_admin
from apps.users.models import CustomUser


class TeamCreationTest(TestCase):
    def test_create_for_user(self):
        email = "alice@example.com"
        user = CustomUser.objects.create(
            username=email,
            email=email,
        )
        team = create_default_team_for_user(user)
        self.assertEqual("Alice", team.name)
        self.assertEqual("alice", team.slug)
        self.assertTrue(is_admin(user, team))
        membership = team.membership_set.filter(user=user).first()
        self.assertEqual([SUPER_ADMIN_GROUP], [group.name for group in membership.groups.all()])
