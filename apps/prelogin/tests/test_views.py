import pytest
from django.urls import reverse

from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_home_renders_for_anonymous_user(client):
    response = client.get(reverse("prelogin:home"))
    assert response.status_code == 200
    assert b"The responsible layer between AI" in response.content


@pytest.mark.django_db()
def test_home_redirects_authenticated_user_to_dashboard(client):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client.force_login(user)
    response = client.get(reverse("prelogin:home"))
    assert response.status_code == 302
    assert response.url == reverse("dashboard:index", kwargs={"team_slug": team.slug})


@pytest.mark.django_db()
def test_platform_redirects_to_home_anchor(client):
    response = client.get(reverse("prelogin:platform"))
    assert response.status_code == 301
    assert response.url == "/#how-it-works"


@pytest.mark.django_db()
def test_about_page_renders(client):
    response = client.get(reverse("prelogin:about"))
    assert response.status_code == 200
    assert b"Community" in response.content


@pytest.mark.django_db()
def test_applications_page_renders(client):
    response = client.get(reverse("prelogin:applications"))
    assert response.status_code == 200
    assert b"Use Cases" in response.content


@pytest.mark.django_db()
def test_contact_page_renders(client):
    response = client.get(reverse("prelogin:contact"))
    assert response.status_code == 200
    assert b"hubspot-form" in response.content


@pytest.mark.django_db()
def test_open_opportunities_page_renders(client):
    response = client.get(reverse("prelogin:open_opportunities"))
    assert response.status_code == 200
    assert b"Expression of Interest" in response.content
