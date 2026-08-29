import pytest
from django.urls import reverse

from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.mark.django_db()
def test_non_admin_team_form_is_disabled(client):
    team = TeamFactory()
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    member = UserFactory(email="member@example.org")
    add_user_to_team(team, member)

    client.force_login(member)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert response.status_code == 200
    assert response.context["team_form"].fields["name"].disabled is True

    original_name = team.name
    client.post(reverse("single_team:manage_team", args=[team.slug]), {"name": "Hacked"})
    team.refresh_from_db()
    assert team.name == original_name


@pytest.mark.django_db()
def test_data_nav_link_hidden_for_non_admin(client):
    team = TeamFactory()
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    member = UserFactory(email="member@example.org")
    add_user_to_team(team, member)

    client.force_login(member)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert b'href="#data"' not in response.content


@pytest.mark.django_db()
def test_data_nav_link_shown_for_admin(client):
    team = TeamFactory()
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)

    client.force_login(admin)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert b'href="#data"' in response.content


@pytest.mark.django_db()
def test_admin_team_form_is_not_disabled(client):
    team = TeamFactory()
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)

    client.force_login(admin)
    response = client.get(reverse("single_team:manage_team", args=[team.slug]))

    assert response.context["team_form"].fields["name"].disabled is False
