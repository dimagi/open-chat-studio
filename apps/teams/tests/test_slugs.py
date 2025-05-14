from django.test import TestCase

from apps.teams.helpers import get_next_unique_team_slug
from apps.teams.models import Team


class UniqueSlugTest(TestCase):
    def test_unique_slug_no_conflict(self):
        assert get_next_unique_team_slug("A Slug") == "a-slug"

    def test_unique_slug_conflicts(self):
        Team.objects.create(name="A Team", slug="a-slug")
        assert get_next_unique_team_slug("A Slug") == "a-slug-2"
        Team.objects.create(name="A Team", slug="a-slug-2")
        Team.objects.create(name="A Team", slug="a-slug-4")
        assert get_next_unique_team_slug("A Slug") == "a-slug-3"
        Team.objects.create(name="A Team", slug="a-slug-3")
        assert get_next_unique_team_slug("A Slug") == "a-slug-5"
