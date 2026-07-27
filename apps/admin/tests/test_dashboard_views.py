import pytest
from django.urls import reverse

from apps.users.models import CustomUser
from apps.utils.factories.team import TeamFactory

SECTION_NAMES = [
    "section_growth",
    "section_team_activity",
    "section_charts",
    "section_top_teams",
    "section_platform",
    "section_top_experiments",
    "section_whatsapp",
]

DATE_RANGE = {"range_type": "d30", "start": "2026-05-01", "end": "2026-05-31"}
INVALID_RANGE = {"range_type": "custom", "start": "not-a-date", "end": "2026-05-31"}


@pytest.fixture()
def staff_client(client):
    staff = CustomUser.objects.create(username="staff@acme.com", is_staff=True)
    client.force_login(staff)
    return client


@pytest.mark.django_db()
class TestDashboardSkeleton:
    def test_non_staff_blocked(self, client):
        client.force_login(CustomUser.objects.create(username="member@acme.com"))
        response = client.get(reverse("ocs_admin:usage_chart"), DATE_RANGE)
        assert response.status_code == 302  # user_passes_test redirects to login_url

    def test_skeleton_renders_export_buttons_without_querying_data(self, staff_client):
        """The skeleton must render the export buttons (and section placeholders) even when
        the underlying aggregation queries would be slow, so the buttons survive a 502 in
        any individual section."""
        response = staff_client.get(reverse("ocs_admin:usage_chart"), DATE_RANGE)

        assert response.status_code == 200
        content = response.content.decode()
        # Export buttons render immediately, carrying the date-range querystring.
        assert reverse("ocs_admin:export_usage") in content
        assert reverse("ocs_admin:export_top_teams") in content
        assert "start=2026-05-01" in content
        # Each section is wired up to lazy-load independently.
        for name in SECTION_NAMES:
            assert reverse(f"ocs_admin:{name}") in content

    def test_invalid_range_redirects_home(self, staff_client):
        response = staff_client.get(reverse("ocs_admin:usage_chart"), INVALID_RANGE)
        assert response.status_code == 302
        assert response.url == reverse("ocs_admin:home")


@pytest.mark.django_db()
class TestDashboardSections:
    @pytest.mark.parametrize("section", SECTION_NAMES)
    def test_section_returns_ok_for_staff(self, staff_client, section):
        response = staff_client.get(reverse(f"ocs_admin:{section}"), DATE_RANGE)
        assert response.status_code == 200

    @pytest.mark.parametrize("section", SECTION_NAMES)
    def test_section_blocks_non_staff(self, client, section):
        client.force_login(CustomUser.objects.create(username=f"member-{section}@acme.com"))
        response = client.get(reverse(f"ocs_admin:{section}"), DATE_RANGE)
        assert response.status_code == 302

    @pytest.mark.parametrize("section", SECTION_NAMES)
    def test_section_returns_empty_on_invalid_range(self, staff_client, section):
        """A section with an invalid date range returns empty content rather than erroring,
        so a bad form value can't 500 the fragment request."""
        response = staff_client.get(reverse(f"ocs_admin:{section}"), INVALID_RANGE)
        assert response.status_code == 200
        assert response.content.decode().strip() == ""


@pytest.mark.django_db()
class TestTeamDetail:
    def test_blocks_non_staff(self, client):
        team = TeamFactory.create()
        client.force_login(CustomUser.objects.create(username="member-detail@acme.com"))
        response = client.get(reverse("ocs_admin:team_detail", args=[team.slug]))
        assert response.status_code == 302

    def test_renders_stats_and_metadata_form(self, staff_client, settings):
        settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]
        team = TeamFactory.create(name="Acme", metadata={"team_owner": "Jane Doe"})
        response = staff_client.get(reverse("ocs_admin:team_detail", args=[team.slug]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Acme" in content
        assert "Team Owner" in content
        assert "Jane Doe" in content

    def test_saves_metadata_on_post(self, staff_client, settings):
        settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]
        team = TeamFactory.create(metadata={"team_owner": "Old"})
        response = staff_client.post(
            reverse("ocs_admin:team_detail", args=[team.slug]),
            {"team_owner": "New Owner"},
        )
        assert response.status_code == 302
        team.refresh_from_db()
        assert team.metadata["team_owner"] == "New Owner"


@pytest.mark.django_db()
class TestTeamsApi:
    def test_returns_slug_for_navigation(self, staff_client):
        # Staff (not superuser): the search box is rendered in a staff-level section,
        # so the endpoint it drives must be reachable by staff.
        TeamFactory.create(name="Searchable Team", slug="searchable-team")
        response = staff_client.get(reverse("ocs_admin:teams_api"), {"q": "Searchable"})
        assert response.status_code == 200
        data = response.json()
        assert {"value", "text", "slug"} <= set(data[0])
        assert data[0]["slug"] == "searchable-team"
