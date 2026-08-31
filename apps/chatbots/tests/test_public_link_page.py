"""The public page (spec D5): a kiosk widget on the OCS host, one state banner per refusal."""

import pytest
from django.contrib.sites.models import Site
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import override_settings
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.chatbots.public_link import CSP
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory
from apps.utils.factories.user import UserFactory
from apps.web.meta import get_server_root

TOKEN = "public_token_1234567890123456789012"
CANONICAL = "ocs.example.com"
OTHER_HOST = "other.example.com"


@pytest.fixture(autouse=True)
def _canonical_site(db, settings):
    Site.objects.filter(id=1).update(domain=CANONICAL)
    Site.objects.clear_cache()
    settings.ALLOWED_HOSTS = [CANONICAL, OTHER_HOST, "testserver"]
    yield
    Site.objects.clear_cache()


def _channel(team, *, consent=False, publish=True, enabled=True):
    experiment = ExperimentFactory.create(
        team=team, name="Clinic bot", consent_form=ConsentFormFactory.create(team=team) if consent else None
    )
    if publish:
        experiment.create_new_version(make_default=True)
    return ExperimentChannelFactory.create(
        team=team,
        experiment=experiment,
        platform=ChannelPlatform.PUBLIC,
        enabled=enabled,
        disabled_message="Back soon",
        extra_data={"widget_token": TOKEN, "welcome_messages": ["Hi there"], "starter_questions": ["Hours?"]},
    )


def _get(client, token=TOKEN, host=CANONICAL):
    return client.get(reverse("public_link", args=[token]), HTTP_HOST=host)


@pytest.mark.django_db()
def test_live_page_renders_the_kiosk_widget(client, team_with_users):
    _channel(team_with_users)
    response = _get(client)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'mode="kiosk"' in html
    assert f'embed-key="{TOKEN}"' in html
    assert 'persistent-session="tab"' in html
    assert f'api-base-url="https://{CANONICAL}"' in html or f'api-base-url="http://{CANONICAL}"' in html
    assert "Hi there" in html
    assert "Hours?" in html
    assert "Clinic bot" in html
    assert "unpkg.com/open-chat-studio-widget" in html
    assert response["X-Robots-Tag"] == "noindex"
    assert response["Referrer-Policy"] == "origin"
    assert response["Content-Security-Policy"] == CSP


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("kwargs", "banner"),
    [
        pytest.param({"enabled": False}, "Back soon", id="disabled"),
        pytest.param({"publish": False}, "not published", id="no-published-version"),
        pytest.param({"consent": True}, "consent", id="consent-unavailable"),
    ],
)
def test_refused_states_render_a_banner_and_a_disabled_widget(client, team_with_users, kwargs, banner):
    _channel(team_with_users, **kwargs)
    response = _get(client)
    assert response.status_code == 200
    html = response.content.decode()
    assert banner.lower() in html.lower()
    assert 'disabled="true"' in html


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "enabled",
    [pytest.param(True, id="live"), pytest.param(False, id="disabled")],
)
def test_the_page_names_the_published_chatbot_not_the_draft(client, team_with_users, enabled):
    """A draft moves on after publishing. Visitors only ever reach the published version, so a
    later rename stays internal whether the channel is serving or switched off."""
    channel = _channel(team_with_users, enabled=enabled)
    working = channel.experiment
    working.name = "Internal rename"
    working.description = "Notes for the team"
    working.save()

    html = _get(client).content.decode()

    assert "Internal rename" not in html
    assert "Notes for the team" not in html
    assert "Clinic bot" in html


@pytest.mark.django_db()
def test_unknown_token_is_404(client, team_with_users):
    _channel(team_with_users)
    assert _get(client, token="nope_nope_nope_nope_nope_nope_nop").status_code == 404


@pytest.mark.django_db()
def test_non_canonical_host_is_404(client, team_with_users):
    _channel(team_with_users)
    assert _get(client, host=OTHER_HOST).status_code == 404


@pytest.mark.django_db()
def test_non_canonical_host_names_both_hosts_for_a_signed_in_user(client, team_with_users):
    """A link tried on the wrong host looks the same as a typo without this. Signing in is
    what separates someone debugging the deployment from a passing visitor."""
    _channel(team_with_users)
    client.force_login(team_with_users.members.first())

    response = _get(client, host=OTHER_HOST)

    assert response.status_code == 404
    html = response.content.decode()
    assert f'You reached this one on <span class="font-bold">{OTHER_HOST}</span>' in html
    assert f'Public links are served from <span class="font-bold">{get_server_root()}</span>' in html


@pytest.mark.django_db()
def test_non_canonical_host_tells_an_anonymous_visitor_nothing(client, team_with_users):
    _channel(team_with_users)

    response = _get(client, host=OTHER_HOST)

    assert response.status_code == 404
    assert "Public links are served from" not in response.content.decode()


@pytest.mark.django_db()
def test_deleted_channel_is_404(client, team_with_users):
    channel = _channel(team_with_users)
    channel.soft_delete()
    assert _get(client).status_code == 404


@pytest.mark.django_db()
def test_logged_in_visitor_gets_a_user_id(client, team_with_users):
    _channel(team_with_users)
    user = team_with_users.members.first()
    client.force_login(user)
    html = _get(client).content.decode()
    assert f'user-id="{user.email}"' in html


@pytest.mark.django_db()
@override_settings(RATE_LIMITS={"public_chat": {"rate": "2/5m", "fail_open": True}}, RATE_LIMIT_ENFORCE=True)
def test_page_is_throttled_per_ip(client, team_with_users):
    caches["rate_limit"].clear()
    default_cache.clear()
    _channel(team_with_users)
    _get(client)
    _get(client)
    assert _get(client).status_code == 429


@pytest.mark.django_db()
def test_robots_disallows_the_public_prefix(client):
    response = client.get("/robots.txt")
    assert b"Disallow: /c/" in response.content


@pytest.mark.django_db()
def test_chatbot_home_shows_a_copy_chip_for_the_public_link(client, team_with_users):
    channel = _channel(team_with_users)
    client.force_login(team_with_users.members.first())
    url = reverse("chatbots:single_chatbot_home", args=[team_with_users.slug, channel.experiment_id])
    html = client.get(url, HTTP_HOST=CANONICAL).content.decode()
    assert channel.public_url in html
    assert f'<input id="public-link-{channel.id}" type="hidden"' in html


@pytest.mark.django_db()
def test_logged_in_non_member_gets_no_user_id(client, team_with_users):
    _channel(team_with_users)
    client.force_login(UserFactory.create())
    html = _get(client).content.decode()
    assert "user-id=" not in html
    assert "user-name=" not in html


@pytest.mark.django_db()
def test_team_member_gets_a_live_widget_on_an_unpublished_chatbot(client, team_with_users):
    _channel(team_with_users, publish=False)
    client.force_login(team_with_users.members.first())
    html = _get(client).content.decode()
    assert "not published" in html.lower()
    assert 'disabled="true"' not in html


@pytest.mark.django_db()
def test_team_member_gets_a_disabled_widget_on_a_disabled_channel(client, team_with_users):
    _channel(team_with_users, enabled=False)
    client.force_login(team_with_users.members.first())
    html = _get(client).content.decode()
    assert "Back soon" in html
    assert 'disabled="true"' in html


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("host", "expected_status"),
    [
        pytest.param("[2001:db8::1]:8000", 200, id="same-ipv6-host"),
        pytest.param("[2001:db8::2]:8000", 404, id="other-ipv6-host"),
    ],
)
def test_page_host_check_on_an_ipv6_site(client, team_with_users, settings, host, expected_status):
    settings.ALLOWED_HOSTS = ["[2001:db8::1]", "[2001:db8::2]"]
    Site.objects.filter(id=1).update(domain="[2001:db8::1]:8000")
    Site.objects.clear_cache()
    _channel(team_with_users)
    assert _get(client, host=host).status_code == expected_status
