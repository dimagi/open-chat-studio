import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.admin.imports import import_team_metadata_from_csv
from apps.users.models import CustomUser
from apps.utils.factories.team import TeamFactory

METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]
TIER_FIELDS = [{"key": "tier", "label": "Tier", "type": "select", "options": ["Free", "Paid"]}]


def _csv(content: str):
    return io.BytesIO(content.encode("utf-8"))


@pytest.mark.django_db()
class TestImportTeamMetadataFromCsv:
    def test_updates_teams_matched_by_slug(self, settings):
        settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
        team = TeamFactory.create(slug="team-a", metadata={})

        result = import_team_metadata_from_csv(_csv("Team,Slug,Team Owner\nTeam A,team-a,Jane Doe\n"))

        assert result.updated == ["team-a"]
        assert result.errors == []
        team.refresh_from_db()
        assert team.metadata == {"team_owner": "Jane Doe"}

    def test_preserves_unconfigured_metadata_keys(self, settings):
        settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
        team = TeamFactory.create(slug="team-a", metadata={"legacy": "keep"})

        import_team_metadata_from_csv(_csv("Slug,Team Owner\nteam-a,Jane Doe\n"))

        team.refresh_from_db()
        assert team.metadata == {"legacy": "keep", "team_owner": "Jane Doe"}

    def test_unknown_slug_is_reported_and_others_still_update(self, settings):
        settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
        team = TeamFactory.create(slug="team-a", metadata={})

        result = import_team_metadata_from_csv(_csv("Slug,Team Owner\nmissing,Nobody\nteam-a,Jane Doe\n"))

        assert result.updated == ["team-a"]
        assert "no team with slug 'missing'" in result.errors[0]
        team.refresh_from_db()
        assert team.metadata == {"team_owner": "Jane Doe"}

    def test_missing_slug_column_errors(self, settings):
        settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
        result = import_team_metadata_from_csv(_csv("Team,Team Owner\nTeam A,Jane Doe\n"))

        assert result.updated == []
        assert "must include a 'Slug' column" in result.errors[0]

    def test_no_matching_columns_errors(self, settings):
        settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
        TeamFactory.create(slug="team-a")
        result = import_team_metadata_from_csv(_csv("Slug,Unrelated\nteam-a,x\n"))

        assert result.updated == []
        assert "no columns matching" in result.errors[0]

    def test_select_value_outside_options_is_rejected(self, settings):
        settings.TEAM_METADATA_FIELDS = TIER_FIELDS
        team = TeamFactory.create(slug="team-a", metadata={})

        result = import_team_metadata_from_csv(_csv("Slug,Tier\nteam-a,Enterprise\n"))

        assert result.updated == []
        assert "not a valid option for 'Tier'" in result.errors[0]
        team.refresh_from_db()
        assert team.metadata == {}

    def test_valid_select_value_is_accepted(self, settings):
        settings.TEAM_METADATA_FIELDS = TIER_FIELDS
        team = TeamFactory.create(slug="team-a", metadata={})

        result = import_team_metadata_from_csv(_csv("Slug,Tier\nteam-a,Paid\n"))

        assert result.updated == ["team-a"]
        team.refresh_from_db()
        assert team.metadata == {"tier": "Paid"}

    def test_invalid_email_is_rejected(self, settings):
        settings.TEAM_METADATA_FIELDS = [{"key": "contact", "label": "Contact", "type": "email"}]
        team = TeamFactory.create(slug="team-a", metadata={})

        result = import_team_metadata_from_csv(_csv("Slug,Contact\nteam-a,not-an-email\n"))

        assert result.updated == []
        assert "not a valid email for 'Contact'" in result.errors[0]
        team.refresh_from_db()
        assert team.metadata == {}

    def test_blank_value_clears_without_validation(self, settings):
        settings.TEAM_METADATA_FIELDS = TIER_FIELDS
        team = TeamFactory.create(slug="team-a", metadata={"tier": "Paid"})

        result = import_team_metadata_from_csv(_csv("Slug,Tier\nteam-a,\n"))

        assert result.updated == ["team-a"]
        team.refresh_from_db()
        assert team.metadata == {"tier": ""}


@pytest.mark.django_db()
class TestImportTeamMetadataView:
    def _url(self):
        return reverse("ocs_admin:import_team_metadata")

    def test_non_staff_blocked(self, client):
        user = CustomUser.objects.create(username="member@acme.com")
        client.force_login(user)
        response = client.get(self._url())
        assert response.status_code == 302  # user_passes_test redirects to login_url

    def test_staff_can_import(self, client, settings):
        settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
        staff = CustomUser.objects.create(username="staff@acme.com", is_staff=True)
        team = TeamFactory.create(slug="team-a", metadata={})
        client.force_login(staff)

        upload = SimpleUploadedFile("metadata.csv", b"Slug,Team Owner\nteam-a,Jane Doe\n", content_type="text/csv")
        response = client.post(self._url(), {"file": upload})

        assert response.status_code == 200
        assert response.context["result"].updated == ["team-a"]
        team.refresh_from_db()
        assert team.metadata == {"team_owner": "Jane Doe"}
