from django.test import TestCase

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
