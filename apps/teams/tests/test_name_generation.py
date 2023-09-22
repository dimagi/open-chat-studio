from django.test import SimpleTestCase

from apps.teams.helpers import get_default_team_name_for_user
from apps.users.models import CustomUser


class TeamNameGeneratorTest(SimpleTestCase):
    def test_name_generation_default(self):
        self.assertEqual("My Team", get_default_team_name_for_user(CustomUser()))

    def test_name_generation_via_email(self):
        self.assertEqual("Guido", get_default_team_name_for_user(CustomUser(email="guido@example.com")))

    def test_name_generation_via_username(self):
        self.assertEqual("Guido", get_default_team_name_for_user(CustomUser(email="guido@example.com")))

    def test_name_generation_via_name(self):
        self.assertEqual("Guido", get_default_team_name_for_user(CustomUser(first_name="Guido")))
        self.assertEqual(
            "Guido Rossum", get_default_team_name_for_user(CustomUser(first_name="Guido", last_name="Rossum"))
        )

    def test_name_generation_precedence(self):
        self.assertEqual(
            "Guido", get_default_team_name_for_user(CustomUser(first_name="Guido", email="python@example.com"))
        )
