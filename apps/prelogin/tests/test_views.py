import pytest
from django.conf import settings
from django.urls import reverse

from apps.utils.factories.team import TeamWithUsersFactory

MARKETING = settings.PROJECT_METADATA["MARKETING_SITE_URL"]


@pytest.mark.django_db()
def test_landing_page_renders_for_anonymous_user(client):
    response = client.get(reverse("prelogin:home"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Open-source AI chatbots for" in content
    assert reverse("sso:login") in content
    # The landing page signposts the marketing site rather than duplicating it, and
    # names it as canonical so the two hosts don't compete for the same content.
    assert f'<link rel="canonical" href="{MARKETING}/">' in content


@pytest.mark.django_db()
def test_landing_page_does_not_reintroduce_the_marketing_content(client):
    """The whole point of the teardown: this host serves a signpost, not a second copy."""
    content = client.get(reverse("prelogin:home")).content.decode()
    assert "<open-chat-studio-widget" not in content
    assert "js.hsforms.net" not in content
    assert "Expression of Interest" not in content


@pytest.mark.django_db()
@pytest.mark.parametrize("path", ["/", "/accounts/login/", "/accounts/signup/"])
def test_prelogin_frame_leaks_no_unrendered_template_syntax(client, path):
    """Django's {# ... #} comment is SINGLE-LINE only.

    A multi-line one isn't recognised by the lexer, so it renders as visible text at
    the top of the page — which is exactly what happened while writing landing.html,
    and no content assertion caught it. Covers the auth pages too, since they share
    the frame.
    """
    content = client.get(path).content.decode()
    for token in ["{#", "#}", "{%", "%}", "{{", "}}"]:
        assert token not in content, f"{path} leaked {token!r}"


@pytest.mark.django_db()
def test_home_redirects_authenticated_user_to_dashboard(client):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client.force_login(user)
    response = client.get(reverse("prelogin:home"))
    assert response.status_code == 302
    assert response.url == reverse("dashboard:index", kwargs={"team_slug": team.slug})


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("url_name", "expected"),
    [
        ("about", f"{MARKETING}/about/"),
        ("contact", f"{MARKETING}/contact/"),
        ("applications", f"{MARKETING}/applications/"),
        ("open_opportunities", f"{MARKETING}/open-opportunities/"),
        # Straight to the anchor: /platform/ on the marketing site is itself a 301,
        # so pointing there would make this a two-hop chain.
        ("platform", f"{MARKETING}/#how-it-works"),
    ],
)
def test_retired_marketing_paths_redirect_permanently(client, url_name, expected):
    response = client.get(reverse(f"prelogin:{url_name}"))
    assert response.status_code == 301
    assert response.url == expected


LEGAL_URLS = {
    "PRIVACY_POLICY_URL": "https://dimagi.com/terms-privacy/",
    "TERMS_URL": "https://dimagi.com/terms-of-service/",
    "ACCEPTABLE_USE_POLICY_URL": "https://dimagi.com/terms-aup/",
}


def _footer(content):
    return content[content.index("<footer") :]


@pytest.mark.django_db()
@pytest.mark.parametrize("path", ["/", "/accounts/login/"])
def test_footer_legal_links_follow_the_settings(client, settings, path):
    """Set in PROJECT_METADATA, they render; unset, the whole <li> is omitted.

    Omitted rather than emptied matters: the footer's separators are drawn by CSS on
    `li:not(:last-child)`, so an empty <li> left behind would show as an orphan pipe.
    """
    settings.PROJECT_METADATA = {**settings.PROJECT_METADATA, **LEGAL_URLS}
    footer = _footer(client.get(path).content.decode())
    for url in LEGAL_URLS.values():
        assert url in footer

    settings.PROJECT_METADATA = {**settings.PROJECT_METADATA, **dict.fromkeys(LEGAL_URLS, "")}
    footer = _footer(client.get(path).content.decode())
    for url in LEGAL_URLS.values():
        assert url not in footer
    assert "Acceptable Use Policy" not in footer


@pytest.mark.django_db()
def test_footer_separators_are_not_in_the_markup(client, settings):
    """CSS draws them. A typed-in pipe would orphan whenever an item is gated off."""
    settings.PROJECT_METADATA = {**settings.PROJECT_METADATA, **LEGAL_URLS}
    assert "|" not in _footer(client.get("/").content.decode())


@pytest.mark.django_db()
def test_sitemap_lists_only_the_landing_page(client):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    content = response.content.decode()
    assert content.count("<url>") == 1
    # None of the retired paths, which are redirects now — a sitemap should not
    # nominate a redirect.
    for path in ["/about/", "/contact/", "/applications/", "/open-opportunities/"]:
        assert f"<loc>http://testserver{path}</loc>" not in content
