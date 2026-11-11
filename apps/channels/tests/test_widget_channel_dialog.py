"""The Chat Widget & API channel dialog: one short create form, then Settings and Embed & API tabs."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from apps.channels import widget_versions
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import ALL_DOMAINS
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.fixture()
def experiment(team_with_users):
    return ExperimentFactory(team=team_with_users)


@pytest.fixture()
def admin_client(client, team_with_users):
    user = team_with_users.members.first()
    codenames = ["add_experimentchannel", "change_experimentchannel"]
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    client.force_login(user)
    return client


@pytest.fixture()
def widget_channel(experiment):
    return ExperimentChannelFactory(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "tok123", "allowed_domains": ["example.com", "*.mysite.org"]},
    )


def _create_url(experiment):
    return reverse(
        "channels:channel_create_dialog",
        args=[experiment.team.slug, experiment.id, ChannelPlatform.EMBEDDED_WIDGET.value],
    )


def _create_data(allow_all_domains: str) -> dict:
    platform = ChannelPlatform.EMBEDDED_WIDGET.value
    return {"name": "", "platform": platform, "enabled": "on", "allow_all_domains": allow_all_domains}


def _edit_url(channel):
    return reverse("channels:channel_edit_dialog", args=[channel.team.slug, channel.experiment_id, channel.id])


@pytest.mark.django_db()
def test_edit_dialog_has_settings_and_embed_tabs(admin_client, widget_channel):
    content = admin_client.get(_edit_url(widget_channel)).content.decode()

    assert "Embed &amp; API" in content
    assert "Published Version" in content
    assert "tab: 'settings'" in content
    assert 'name="enabled"' in content
    assert 'name="credential_mode"' in content
    assert 'data-domains="example.com\n*.mysite.org"' in content
    assert 'id="widget_embed_code"' in content
    assert "Delete channel" in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("reported_version", "shows_update"),
    [
        pytest.param("0.0.1", True, id="outdated-embed"),
        pytest.param(None, False, id="never-reported"),
    ],
)
def test_update_pill_follows_the_reported_version(admin_client, widget_channel, reported_version, shows_update):
    widget_channel.widget_version = reported_version
    widget_channel.save(update_fields=["widget_version"])

    content = admin_client.get(_edit_url(widget_channel)).content.decode()

    assert ("Widget update available" in content) is shows_update
    assert ("Replace the script tag on your site" in content) is shows_update


@pytest.mark.django_db()
def test_create_dialog_is_a_short_form(admin_client, experiment):
    content = admin_client.get(_create_url(experiment)).content.decode()

    assert "Where the widget may be embedded" in content
    assert 'name="credential_mode"' not in content
    assert 'name="session_token_lifetime"' not in content
    assert 'name="enabled" value="on"' in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("allow_all", "domains", "expected"),
    [
        pytest.param("true", "", [ALL_DOMAINS], id="any-domain"),
        pytest.param("false", "example.com\n*.mysite.org", ["example.com", "*.mysite.org"], id="listed-domains"),
    ],
)
def test_create_saves_an_enabled_channel_and_opens_the_embed_tab(
    admin_client, experiment, allow_all, domains, expected
):
    data = _create_data(allow_all)
    if domains:
        data["allowed_domains"] = domains

    response = admin_client.post(_create_url(experiment), data)

    channel = ExperimentChannel.objects.get(experiment=experiment, platform=ChannelPlatform.EMBEDDED_WIDGET)
    assert channel.enabled
    assert channel.extra_data["allowed_domains"] == expected
    assert "tab: 'embed'" in response.content.decode()


@pytest.mark.django_db()
def test_create_without_domains_shows_the_error_on_the_form(admin_client, experiment):
    data = _create_data("false")

    response = admin_client.post(_create_url(experiment), data)

    assert not ExperimentChannel.objects.filter(experiment=experiment).exists()
    assert "You must specify at least one domain" in response.content.decode()


@pytest.mark.django_db()
def test_other_platforms_keep_the_single_column_dialog(admin_client, experiment):
    channel = ExperimentChannelFactory(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "t"}
    )

    content = admin_client.get(_edit_url(channel)).content.decode()

    assert "Embed &amp; API" not in content
    assert "Update" in content


@pytest.mark.django_db()
def test_edit_error_keeps_allow_all_domains_checked(admin_client, widget_channel):
    data = {
        "name": widget_channel.name,
        "platform": ChannelPlatform.EMBEDDED_WIDGET.value,
        "enabled": "on",
        "allow_all_domains": "on",
        "session_token_lifetime": "00:01:00",
    }

    content = admin_client.post(_edit_url(widget_channel), data).content.decode()

    assert "The session lifetime must be at least 5 minutes." in content
    assert "&quot;allowAllDomains&quot;: true" in content


@pytest.mark.django_db()
def test_embed_tab_shows_the_minimum_and_the_reported_version(admin_client, widget_channel):
    widget_channel.widget_version = widget_versions.LATEST_VERSION
    widget_channel.widget_version_updated_at = timezone.now()
    widget_channel.save(update_fields=["widget_version", "widget_version_updated_at"])

    content = admin_client.get(_edit_url(widget_channel)).content.decode()

    assert f"This channel requires widget {widget_channel.min_widget_version} or newer." in content
    assert f"Your site last reported widget {widget_versions.LATEST_VERSION}" in content
