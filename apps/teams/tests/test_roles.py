from django.test import TestCase

from apps.teams.models import Team
from apps.teams.roles import ROLE_ADMIN, ROLE_MEMBER, is_admin, is_member
from apps.users.models import CustomUser


class RoleTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team1 = Team.objects.create(name="Team 1", slug="team-1")
        cls.team2 = Team.objects.create(name="Team 2", slug="team-2")

    def test_admin_role(self):
        user = CustomUser.objects.create(email="user@example.com")
        self.team1.members.add(user, through_defaults={"role": ROLE_ADMIN})
        self.assertTrue(is_admin(user, self.team1))
        self.assertFalse(is_admin(user, self.team2))
        self.assertTrue(is_member(user, self.team1))
        self.assertFalse(is_member(user, self.team2))

    def test_non_admin_role(self):
        user = CustomUser.objects.create(email="user@example.com")
        self.team1.members.add(user, through_defaults={"role": ROLE_MEMBER})
        self.assertFalse(is_admin(user, self.team1))
        self.assertFalse(is_admin(user, self.team2))
        self.assertTrue(is_member(user, self.team1))
        self.assertFalse(is_member(user, self.team2))
