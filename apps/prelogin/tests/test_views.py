import pytest
from django.urls import reverse

from apps.channels.widget_versions import widget_script_url
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
def test_applications_page_embeds_widget_for_configured_demo_bots(client, settings):
    settings.PRELOGIN_DEMO_BOTS = {"nanibot": {"id": "6d5abc50-167d-4e78-a2a1-6ff6d3cb229c", "embed_key": "token-123"}}
    response = client.get(reverse("prelogin:applications"))
    content = response.content.decode()
    assert 'chatbot-id="6d5abc50-167d-4e78-a2a1-6ff6d3cb229c"' in content
    assert 'embed-key="token-123"' in content
    assert widget_script_url() in content
    # The configured bot's card opens the widget; the other two stay inert.
    assert content.count('data-demo-bot-trigger="nanibot"') == 1
    assert content.count('class="bot-card bot-card-h bot-card-interactive"') == 1
    assert content.count("Try this bot") == 1
    # Every demo widget carries the same demonstration-only banner.
    assert 'banner-message="Example bot: for demonstration and research purposes only."' in content
    assert 'banner-style="info"' in content


@pytest.mark.django_db()
def test_applications_page_cards_are_inert_without_demo_bot_config(client, settings):
    settings.PRELOGIN_DEMO_BOTS = {}
    response = client.get(reverse("prelogin:applications"))
    content = response.content.decode()
    assert "<open-chat-studio-widget" not in content
    assert "data-demo-bot-trigger" not in content
    assert 'bot-card-interactive"' not in content  # the class is only ever in the stylesheet
    assert "Try this bot" not in content
    # No links to the chat UI that replaced these cards.
    assert "/experiments/e/" not in content


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
