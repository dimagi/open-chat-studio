import pytest
from django.test import Client
from django.urls import reverse

from apps.teams.backends import add_user_to_team
from apps.teams.models import Team
from apps.users.models import CustomUser

METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]


@pytest.fixture()
def team():
    return Team.objects.create(name="Acme", slug="acme")


@pytest.fixture()
def staff_member(team):
    user = CustomUser.objects.create(username="staff@acme.com", is_staff=True)
    add_user_to_team(team, user)
    return user


@pytest.fixture()
def member(team):
    user = CustomUser.objects.create(username="member@acme.com")
    add_user_to_team(team, user)
    return user


def _url(team):
    return reverse("single_team:internal_metadata", args=[team.slug])


@pytest.mark.django_db()
def test_staff_can_view(team, staff_member, settings):
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    client = Client()
    client.force_login(staff_member)
    response = client.get(_url(team))
    assert response.status_code == 200
    assert b"Team Owner" in response.content


@pytest.mark.django_db()
def test_non_staff_member_gets_404(team, member, settings):
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    client = Client()
    client.force_login(member)
    response = client.get(_url(team))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_staff_can_save_metadata(team, staff_member, settings):
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    client = Client()
    client.force_login(staff_member)
    response = client.post(_url(team), {"team_owner": "Jane Doe"}, follow=True)
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.metadata == {"team_owner": "Jane Doe"}


@pytest.mark.django_db()
def test_save_preserves_unconfigured_metadata(team, staff_member, settings):
    """Editing the configured fields must not drop metadata keys that aren't in settings."""
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    team.metadata = {"legacy_key": "keep me"}
    team.save()

    client = Client()
    client.force_login(staff_member)
    client.post(_url(team), {"team_owner": "Jane Doe"})

    team.refresh_from_db()
    assert team.metadata == {"legacy_key": "keep me", "team_owner": "Jane Doe"}
