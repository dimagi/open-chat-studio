import pytest
from django.urls import reverse
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.teams.forms import TeamPublicKeyForm
from apps.teams.models import Team
from apps.users.models import CustomUser

PUBLIC_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8Abase64encodedkey=="


@pytest.fixture()
def team():
    return Team.objects.create(name="Acme", slug="acme")


@pytest.fixture()
def admin(team):
    user = CustomUser.objects.create(username="admin@acme.com")
    make_user_team_owner(team, user)
    return user


@pytest.fixture()
def member(team):
    user = CustomUser.objects.create(username="member@acme.com")
    add_user_to_team(team, user)
    return user


def _set_public_key_url(team):
    return reverse("single_team:set_public_key", args=[team.slug])


def _manage_team_url(team):
    return reverse("single_team:manage_team", args=[team.slug])


@pytest.mark.django_db()
class TestSetPublicKey:
    def test_form_accepts_public_key(self):
        form = TeamPublicKeyForm(data={"public_key": PUBLIC_KEY})
        assert form.is_valid(), form.errors

    def test_admin_can_set_public_key(self, client, team, admin):
        client.force_login(admin)
        response = client.post(_set_public_key_url(team), {"public_key": PUBLIC_KEY})
        assert response.status_code == 302
        team.refresh_from_db()
        assert team.public_key == PUBLIC_KEY

    def test_member_cannot_set_public_key(self, client, team, member):
        client.force_login(member)
        response = client.post(_set_public_key_url(team), {"public_key": PUBLIC_KEY})
        assert response.status_code == 403
        team.refresh_from_db()
        assert team.public_key == ""

    def test_data_export_section_visible_to_admin(self, client, team, admin):
        client.force_login(admin)
        response = client.get(_manage_team_url(team))
        assert response.status_code == 200
        assert "Data Export" in response.content.decode()

    def test_data_export_section_hidden_from_member(self, client, team, member):
        client.force_login(member)
        response = client.get(_manage_team_url(team))
        assert response.status_code == 200
        assert "Data Export" not in response.content.decode()

    def test_setting_public_key_is_audited(self, client, team, admin):
        client.force_login(admin)
        with enable_audit():
            client.post(_set_public_key_url(team), {"public_key": PUBLIC_KEY})
        events = AuditEvent.objects.by_model(Team).filter(object_pk=team.id)
        assert any("public_key" in (event.delta or {}) for event in events)
