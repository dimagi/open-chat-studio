from django.test import SimpleTestCase

from apps.teams.helpers import get_default_team_name_for_user
from apps.users.models import CustomUser


class TeamNameGeneratorTest(SimpleTestCase):
    def test_name_generation_default(self):
        assert get_default_team_name_for_user(CustomUser()) == "My Team"

    def test_name_generation_via_email(self):
        assert get_default_team_name_for_user(CustomUser(email="guido@example.com")) == "Guido"

    def test_name_generation_via_username(self):
        assert get_default_team_name_for_user(CustomUser(email="guido@example.com")) == "Guido"

    def test_name_generation_via_name(self):
        assert get_default_team_name_for_user(CustomUser(first_name="Guido")) == "Guido"
        assert get_default_team_name_for_user(CustomUser(first_name="Guido", last_name="Rossum")) == "Guido Rossum"

    def test_name_generation_precedence(self):
        assert get_default_team_name_for_user(CustomUser(first_name="Guido", email="python@example.com")) == "Guido"
