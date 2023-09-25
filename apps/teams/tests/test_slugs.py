from django.test import TestCase

from apps.teams.helpers import get_next_unique_team_slug
from apps.teams.models import Team


class UniqueSlugTest(TestCase):
    def test_unique_slug_no_conflict(self):
        self.assertEqual("a-slug", get_next_unique_team_slug("A Slug"))

    def test_unique_slug_conflicts(self):
        Team.objects.create(name="A Team", slug="a-slug")
        self.assertEqual("a-slug-2", get_next_unique_team_slug("A Slug"))
        Team.objects.create(name="A Team", slug="a-slug-2")
        Team.objects.create(name="A Team", slug="a-slug-4")
        self.assertEqual("a-slug-3", get_next_unique_team_slug("A Slug"))
        Team.objects.create(name="A Team", slug="a-slug-3")
        self.assertEqual("a-slug-5", get_next_unique_team_slug("A Slug"))
