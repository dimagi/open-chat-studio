"""The public page (spec D5): a kiosk widget on the OCS host, one state banner per refusal."""

import pytest
from django.contrib.sites.models import Site
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import override_settings
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.teams.models import Flag
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory

TOKEN = "public_token_1234567890123456789012"
CANONICAL = "ocs.example.com"


@pytest.fixture(autouse=True)
def _canonical_site(db, settings):
    Site.objects.filter(id=1).update(domain=CANONICAL)
    Site.objects.clear_cache()
    settings.ALLOWED_HOSTS = [CANONICAL, "other.example.com", "testserver"]
    yield
    Site.objects.clear_cache()


@pytest.fixture(autouse=True)
def _public_channel_flag_enabled(db, team_with_users):
    flag = Flag.objects.create(name="flag_public_channel")
    flag.teams.add(team_with_users)
    flag.flush()
    return flag


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
    assert "unpkg.com" in response["Content-Security-Policy"]
    assert "cdnjs.cloudflare.com" in response["Content-Security-Policy"]


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
def test_unknown_token_is_404(client, team_with_users):
    _channel(team_with_users)
    assert _get(client, token="nope_nope_nope_nope_nope_nope_nop").status_code == 404


@pytest.mark.django_db()
def test_non_canonical_host_is_404(client, team_with_users):
    _channel(team_with_users)
    assert _get(client, host="other.example.com").status_code == 404


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
