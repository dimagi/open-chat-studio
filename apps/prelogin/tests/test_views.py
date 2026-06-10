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
    assert b"collaborate" in response.content


@pytest.mark.django_db()
def test_contact_page_shows_hubspot_form_when_configured(client, settings):
    settings.HUBSPOT_FORM_PORTAL_ID = "503070"
    settings.HUBSPOT_FORM_ID = "ab84dc67-539d-40d3-b9ac-466d8b8348bf"
    response = client.get(reverse("prelogin:contact"))
    content = response.content.decode()
    assert 'id="hubspot-form"' in content
    assert "js.hsforms.net" in content
    assert "503070" in content


@pytest.mark.django_db()
def test_contact_page_hides_hubspot_form_when_not_configured(client, settings):
    settings.HUBSPOT_FORM_PORTAL_ID = ""
    settings.HUBSPOT_FORM_ID = ""
    response = client.get(reverse("prelogin:contact"))
    content = response.content.decode()
    assert 'id="hubspot-form"' not in content
    assert "js.hsforms.net" not in content


@pytest.mark.django_db()
def test_contact_page_shows_contact_email_when_configured(client, settings):
    settings.HUBSPOT_FORM_PORTAL_ID = ""
    settings.HUBSPOT_FORM_ID = ""
    settings.PRELOGIN_CONTACT_EMAIL = "hello@example.com"
    response = client.get(reverse("prelogin:contact"))
    assert b"mailto:hello@example.com" in response.content


@pytest.mark.django_db()
def test_contact_page_omits_email_when_not_configured(client, settings):
    settings.HUBSPOT_FORM_PORTAL_ID = ""
    settings.HUBSPOT_FORM_ID = ""
    settings.PRELOGIN_CONTACT_EMAIL = ""
    response = client.get(reverse("prelogin:contact"))
    assert b"mailto:" not in response.content


@pytest.mark.django_db()
def test_open_opportunities_page_renders(client):
    response = client.get(reverse("prelogin:open_opportunities"))
    assert response.status_code == 200
    assert b"Expression of Interest" in response.content


@pytest.mark.django_db()
def test_sitemap_lists_prelogin_pages(client):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    content = response.content.decode()
    for name in ["about", "contact", "applications", "open_opportunities"]:
        assert reverse(f"prelogin:{name}") in content
    # home reverses to "/" which trivially appears in every URL; assert via entry count instead
    assert content.count("<url>") == 5
