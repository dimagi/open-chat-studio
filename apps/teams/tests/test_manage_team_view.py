import pytest
from django.urls import reverse
from waffle.testutils import override_flag

from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.teams.flags import Flags
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


def _section_url(team, section):
    return reverse("single_team:manage_team_section", args=[team.slug, section])


@pytest.fixture()
def team():
    return TeamFactory()


@pytest.fixture()
def admin(team):
    user = UserFactory(email="admin@example.org")
    make_user_team_owner(team, user)
    return user


@pytest.fixture()
def member(team):
    user = UserFactory(email="member@example.org")
    add_user_to_team(team, user)
    return user


@pytest.mark.django_db()
def test_non_admin_team_form_is_disabled(client, team, member):
    client.force_login(member)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert response.status_code == 200
    assert response.context["team_form"].fields["name"].disabled is True

    original_name = team.name
    client.post(reverse("single_team:manage_team", args=[team.slug]), {"name": "Hacked"})
    team.refresh_from_db()
    assert team.name == original_name


@pytest.mark.django_db()
def test_admin_team_form_is_not_disabled(client, team, admin):
    client.force_login(admin)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert response.context["team_form"].fields["name"].disabled is False


@pytest.mark.django_db()
def test_data_nav_link_hidden_for_non_admin(client, team, member):
    client.force_login(member)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert _section_url(team, "data").encode() not in response.content


@pytest.mark.django_db()
def test_data_nav_link_shown_for_admin(client, team, admin):
    client.force_login(admin)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert _section_url(team, "data").encode() in response.content


@pytest.mark.django_db()
def test_notifications_section_hidden_when_flag_off(client, team, admin):
    client.force_login(admin)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert _section_url(team, "notifications").encode() not in response.content
    assert client.get(_section_url(team, "notifications")).status_code == 404


@pytest.mark.django_db()
@override_flag(Flags.SLACK_NOTIFICATIONS.slug, active=True)
def test_notifications_section_shown_when_flag_on(client, team, admin):
    client.force_login(admin)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))
    assert _section_url(team, "notifications").encode() in response.content

    section = client.get(_section_url(team, "notifications"))
    assert section.status_code == 200
    assert b"btn-add-notification-channel" in section.content


@pytest.mark.django_db()
def test_internal_metadata_section_is_staff_only(client, team, admin):
    client.force_login(admin)
    assert client.get(_section_url(team, "internal-metadata")).status_code == 404

    admin.is_staff = True
    admin.save()
    assert client.get(_section_url(team, "internal-metadata")).status_code == 200


@pytest.mark.django_db()
def test_renaming_the_team_keeps_the_current_section(client, team, admin):
    client.force_login(admin)
    response = client.post(_section_url(team, "members"), {"name": "Renamed"})

    team.refresh_from_db()
    assert team.name == "Renamed"
    assert response.context["active_section"].key == "members"
