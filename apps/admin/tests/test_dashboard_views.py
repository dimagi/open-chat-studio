from urllib.parse import quote

import pytest
from django.test import Client
from django.urls import reverse

from apps.users.models import CustomUser
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.elevation import elevate_session
from apps.web.elevation import Grant

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


@pytest.mark.django_db()
class TestDashboardSkeleton:
    def test_non_staff_blocked(self, client):
        client.force_login(CustomUser.objects.create(username="member@acme.com"))
        response = client.get(reverse("ocs_admin:usage_chart"), DATE_RANGE)
        assert response.status_code == 404  # nobody who could not elevate learns the view exists

    def test_staff_without_elevation_is_sent_to_acquire_it(self, client):
        client.force_login(CustomUser.objects.create(username="unelevated@acme.com", is_staff=True))
        url = reverse("ocs_admin:usage_chart")

        response = client.get(url)

        assert response.status_code == 302
        assert response.url == f"{reverse('web:elevate_ocs_admin')}?next={quote(url, safe='')}"

    def test_the_home_page_offers_staff_only_what_they_can_reach(self, staff_client):
        """A staff member should not be shown buttons that 404 on them."""
        superuser = CustomUser.objects.create(username="super@acme.com", is_staff=True, is_superuser=True)
        superuser_client = Client()
        superuser_client.force_login(superuser)
        elevate_session(superuser_client, Grant.OCS_ADMIN)

        staff_view = staff_client.get(reverse("ocs_admin:home")).content.decode()
        superuser_view = superuser_client.get(reverse("ocs_admin:home")).content.decode()

        for url_name in ["configuration", "flags_home", "find_provider_by_key"]:
            assert reverse(f"ocs_admin:{url_name}") not in staff_view
            assert reverse(f"ocs_admin:{url_name}") in superuser_view
        assert reverse("ocs_admin:team_metadata") in staff_view

    def test_staff_elevation_does_not_reach_the_superuser_views(self, staff_client):
        assert staff_client.get(reverse("ocs_admin:flags_home")).status_code == 404
        assert staff_client.get(reverse("ocs_admin:configuration")).status_code == 404
        assert staff_client.get(reverse("ocs_admin:find_provider_by_key")).status_code == 404

    def test_elevating_into_another_grant_does_not_unlock_the_admin(self, client):
        """The grants are namespaced, so a team elevation is not an admin elevation."""
        staff = CustomUser.objects.create(username="team-elevated@acme.com", is_staff=True, is_superuser=True)
        client.force_login(staff)
        elevate_session(client, Grant.team(TeamFactory.create().slug))

        assert client.get(reverse("ocs_admin:usage_chart")).status_code == 302

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
        assert response.status_code == 404

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
        assert response.status_code == 404

    def test_direct_get_redirects_to_manage_page(self, staff_client):
        """A non-HTMX GET is a shareable link into the manage page, not a bare panel."""
        team = TeamFactory.create()
        response = staff_client.get(reverse("ocs_admin:team_detail", args=[team.slug]))
        assert response.status_code == 302
        assert response.url == f"{reverse('ocs_admin:team_metadata')}?team={team.slug}"

    def test_htmx_get_renders_stats_and_metadata_form(self, staff_client, settings):
        settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]
        team = TeamFactory.create(name="Acme", metadata={"team_owner": "Jane Doe"})
        response = staff_client.get(reverse("ocs_admin:team_detail", args=[team.slug]), headers={"HX-Request": "true"})
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
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "New Owner" in response.content.decode()
        team.refresh_from_db()
        assert team.metadata["team_owner"] == "New Owner"


@pytest.mark.django_db()
class TestTeamMetadataPage:
    def test_blocks_non_staff(self, client):
        client.force_login(CustomUser.objects.create(username="member-meta@acme.com"))
        response = client.get(reverse("ocs_admin:team_metadata"))
        assert response.status_code == 404

    def test_renders_import_export_and_search(self, staff_client):
        response = staff_client.get(reverse("ocs_admin:team_metadata"))
        assert response.status_code == 200
        content = response.content.decode()
        assert reverse("ocs_admin:export_team_metadata") in content
        assert reverse("ocs_admin:teams_api") in content
        assert 'id="team-detail-panel"' in content

    def test_preselected_team_loads_panel(self, staff_client):
        team = TeamFactory.create()
        response = staff_client.get(reverse("ocs_admin:team_metadata"), {"team": team.slug})
        assert response.status_code == 200
        assert response.context["initial_team"] == team
        assert reverse("ocs_admin:team_detail", args=[team.slug]) in response.content.decode()

    def test_unknown_team_leaves_panel_empty(self, staff_client):
        response = staff_client.get(reverse("ocs_admin:team_metadata"), {"team": "does-not-exist"})
        assert response.status_code == 200
        assert response.context["initial_team"] is None


@pytest.mark.django_db()
class TestTeamsApi:
    def test_returns_slug_and_creator_for_navigation(self, staff_client):
        # Staff (not superuser): the search box is rendered in a staff-level section,
        # so the endpoint it drives must be reachable by staff.
        creator = UserFactory(username="creator", email="creator@example.com")
        TeamFactory.create(name="Searchable Team", slug="searchable-team", created_by=creator)
        response = staff_client.get(reverse("ocs_admin:teams_api"), {"q": "Searchable"})
        assert response.status_code == 200
        data = response.json()
        assert {"value", "text", "slug", "created_by"} <= set(data[0])
        assert data[0]["slug"] == "searchable-team"
        assert data[0]["created_by"] == {
            "id": creator.id,
            "username": "creator",
            "email": "creator@example.com",
        }

    def test_returns_null_creator_when_unknown(self, staff_client):
        TeamFactory.create(name="Legacy Team", created_by=None)

        data = staff_client.get(reverse("ocs_admin:teams_api"), {"q": "Legacy"}).json()

        assert data[0]["created_by"] is None
