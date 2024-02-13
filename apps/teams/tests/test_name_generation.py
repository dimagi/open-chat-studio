from django.test import SimpleTestCase

from apps.teams.helpers import get_default_team_name_for_user
from apps.users.models import CustomUser


class TeamNameGeneratorTest(SimpleTestCase):
    def test_name_generation_default(self):
        assert "My Team" == get_default_team_name_for_user(CustomUser())

    def test_name_generation_via_email(self):
        assert "Guido" == get_default_team_name_for_user(CustomUser(email="guido@example.com"))

    def test_name_generation_via_username(self):
        assert "Guido" == get_default_team_name_for_user(CustomUser(email="guido@example.com"))

    def test_name_generation_via_name(self):
        assert "Guido" == get_default_team_name_for_user(CustomUser(first_name="Guido"))
        assert "Guido Rossum" == get_default_team_name_for_user(CustomUser(first_name="Guido", last_name="Rossum"))

    def test_name_generation_precedence(self):
        assert "Guido" == get_default_team_name_for_user(CustomUser(first_name="Guido", email="python@example.com"))
