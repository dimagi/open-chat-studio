import pytest
from django.test import Client
from django.urls import reverse

from apps.teams.backends import add_user_to_team
from apps.teams.models import Team
from apps.users.models import CustomUser
from apps.utils.tests.elevation import elevate_session
from apps.web.elevation import Grant

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
def staff_client(staff_member):
    """Staff, holding the OCS admin elevation — internal metadata is behind it."""
    client = Client()
    client.force_login(staff_member)
    elevate_session(client, Grant.OCS_ADMIN)
    return client


@pytest.fixture()
def member(team):
    user = CustomUser.objects.create(username="member@acme.com")
    add_user_to_team(team, user)
    return user


def _url(team):
    return reverse("single_team:internal_metadata", args=[team.slug])


@pytest.mark.django_db()
def test_staff_can_view(team, staff_client, settings):
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    response = staff_client.get(_url(team))
    assert response.status_code == 200
    assert b"Team Owner" in response.content
    assert response.context["breadcrumbs"] == [
        ("Team Settings", reverse("single_team:manage_team", args=[team.slug])),
        ("Internal Metadata", None),
    ]


@pytest.mark.django_db()
def test_non_staff_member_gets_404(team, member, settings):
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    client = Client()
    client.force_login(member)
    response = client.get(_url(team))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_staff_can_save_metadata(team, staff_client, settings):
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    response = staff_client.post(_url(team), {"team_owner": "Jane Doe"}, follow=True)
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.metadata == {"team_owner": "Jane Doe"}


@pytest.mark.django_db()
def test_email_field_rejects_invalid_address(team, staff_client, settings):
    settings.TEAM_METADATA_FIELDS = [{"key": "contact", "label": "Contact", "type": "email"}]
    response = staff_client.post(_url(team), {"contact": "not-an-email"})
    assert response.status_code == 200  # redisplays the form with errors
    team.refresh_from_db()
    assert team.metadata == {}


@pytest.mark.django_db()
def test_select_field_rejects_value_outside_options(team, staff_client, settings):
    settings.TEAM_METADATA_FIELDS = [{"key": "tier", "label": "Tier", "type": "select", "options": ["Free", "Paid"]}]

    response = staff_client.post(_url(team), {"tier": "Enterprise"})
    assert response.status_code == 200  # invalid choice redisplays the form
    team.refresh_from_db()
    assert team.metadata == {}

    staff_client.post(_url(team), {"tier": "Paid"}, follow=True)
    team.refresh_from_db()
    assert team.metadata == {"tier": "Paid"}


@pytest.mark.django_db()
def test_save_preserves_unconfigured_metadata(team, staff_client, settings):
    """Editing the configured fields must not drop metadata keys that aren't in settings."""
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    team.metadata = {"legacy_key": "keep me"}
    team.save()

    staff_client.post(_url(team), {"team_owner": "Jane Doe"})

    team.refresh_from_db()
    assert team.metadata == {"legacy_key": "keep me", "team_owner": "Jane Doe"}


@pytest.mark.django_db()
def test_staff_without_elevation_is_sent_to_acquire_it(team, staff_member, settings):
    settings.TEAM_METADATA_FIELDS = METADATA_FIELDS
    client = Client()
    client.force_login(staff_member)

    response = client.get(_url(team))

    assert response.status_code == 302
    assert response.url.startswith(reverse("web:elevate_ocs_admin"))
