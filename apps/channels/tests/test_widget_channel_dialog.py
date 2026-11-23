"""The Chat Widget & API channel dialog: one short create form, then Settings and Embed & API tabs."""

from unittest.mock import PropertyMock, patch

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from apps.channels import widget_versions
from apps.channels.models import ChannelPlatform, CredentialMode, ExperimentChannel, WidgetAuthLevel
from apps.channels.utils import ALL_DOMAINS
from apps.utils.factories.channels import ExperimentChannelFactory


@pytest.fixture()
def channel_admin_client(client, team_with_users):
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
def test_edit_dialog_has_settings_and_embed_tabs(channel_admin_client, widget_channel):
    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert "Embed &amp; API" in content
    assert "tab: 'settings'" in content
    assert 'name="enabled"' in content
    assert 'name="credential_mode"' in content
    assert 'data-domains="example.com\n*.mysite.org"' in content
    assert 'id="widget_embed_code"' in content
    assert "Delete channel" in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("publish", "served_label", "other_label"),
    [
        pytest.param(True, "Published Version", "Working Version", id="published"),
        pytest.param(False, "Working Version", "Published Version", id="unpublished"),
    ],
)
def test_header_names_the_served_version(channel_admin_client, widget_channel, publish, served_label, other_label):
    if publish:
        widget_channel.experiment.create_new_version(make_default=True)

    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert served_label in content
    assert other_label not in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("reported_version", "shows_update"),
    [
        pytest.param("0.0.1", True, id="outdated-embed"),
        pytest.param(None, False, id="never-reported"),
    ],
)
def test_update_pill_follows_the_reported_version(channel_admin_client, widget_channel, reported_version, shows_update):
    widget_channel.widget_version = reported_version
    widget_channel.save(update_fields=["widget_version"])

    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert ("Widget update available" in content) is shows_update
    assert ("Replace the script tag on your site" in content) is shows_update


@pytest.mark.django_db()
def test_create_dialog_is_a_short_form(channel_admin_client, experiment):
    content = channel_admin_client.get(_create_url(experiment)).content.decode()

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
    channel_admin_client, experiment, allow_all, domains, expected
):
    data = _create_data(allow_all)
    if domains:
        data["allowed_domains"] = domains

    response = channel_admin_client.post(_create_url(experiment), data)

    channel = ExperimentChannel.objects.get(experiment=experiment, platform=ChannelPlatform.EMBEDDED_WIDGET)
    assert channel.enabled
    assert channel.extra_data["allowed_domains"] == expected
    assert "tab: 'embed'" in response.content.decode()


@pytest.mark.django_db()
def test_create_without_domains_shows_the_error_on_the_form(channel_admin_client, experiment):
    data = _create_data("false")

    response = channel_admin_client.post(_create_url(experiment), data)

    content = response.content.decode()
    assert not ExperimentChannel.objects.filter(experiment=experiment).exists()
    assert "You must specify at least one domain" in content
    assert 'for="id_allowed_domains"' in content
    assert 'id="id_allowed_domains"' in content
    assert 'aria-describedby="id_allowed_domains_helptext id_allowed_domains_error"' in content
    assert 'id="id_allowed_domains_error"' in content


@pytest.mark.django_db()
def test_other_platforms_keep_the_single_column_dialog(channel_admin_client, experiment):
    channel = ExperimentChannelFactory(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "t"}
    )

    content = channel_admin_client.get(_edit_url(channel)).content.decode()

    assert "Embed &amp; API" not in content
    assert "Update" in content


@pytest.mark.django_db()
def test_edit_error_keeps_allow_all_domains_checked(channel_admin_client, widget_channel):
    data = {
        "name": widget_channel.name,
        "platform": ChannelPlatform.EMBEDDED_WIDGET.value,
        "enabled": "on",
        "allow_all_domains": "on",
        "session_token_lifetime": "00:01:00",
    }

    content = channel_admin_client.post(_edit_url(widget_channel), data).content.decode()

    assert "The session lifetime must be at least 5 minutes." in content
    assert "&quot;allowAllDomains&quot;: true" in content


@pytest.mark.django_db()
@pytest.mark.parametrize("saved_mode", [CredentialMode.EMBED_KEY, CredentialMode.OAUTH])
def test_embed_code_waits_for_a_changed_credential_to_be_saved(channel_admin_client, widget_channel, saved_mode):
    widget_channel.credential_mode = saved_mode
    widget_channel.save(update_fields=["credential_mode"])

    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert f"x-show=\"credentialMode === '{saved_mode.value}'\"" in content
    assert f"x-show=\"credentialMode !== '{saved_mode.value}'\"" in content
    assert "Save to update the embed code." in content


@pytest.mark.django_db()
def test_embed_tab_shows_the_minimum_and_the_reported_version(channel_admin_client, widget_channel):
    widget_channel.widget_version = widget_versions.LATEST_VERSION
    widget_channel.widget_version_updated_at = timezone.now()
    widget_channel.save(update_fields=["widget_version", "widget_version_updated_at"])

    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert f"This channel requires widget {widget_channel.min_widget_version} or newer." in content
    assert f"Your site last reported widget {widget_versions.LATEST_VERSION}" in content


@pytest.mark.django_db()
def test_update_pill_takes_its_style_from_the_update_status(channel_admin_client, widget_channel):
    status = widget_versions.WidgetUpdateStatus(level="error", icon="fa-circle-xmark", message="Unsupported.")
    with patch.object(ExperimentChannel, "widget_update_status", new_callable=PropertyMock, return_value=status):
        content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert "btn-soft btn-error" in content
    assert '<i class="fa-solid fa-circle-xmark"></i> Widget update available' in content


@pytest.mark.django_db()
def test_pending_auth_level_alone_does_not_show_the_update_pill(channel_admin_client, widget_channel):
    widget_channel.widget_version = widget_versions.LATEST_VERSION
    widget_channel.required_auth_level = WidgetAuthLevel.EMBED_KEY
    widget_channel.pending_auth_level = WidgetAuthLevel.SESSION_TOKEN
    widget_channel.auth_level_notified_at = timezone.now()
    widget_channel.save()

    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert "Widget update available" not in content
    assert f"Widget {widget_channel.pending_min_widget_version} required from" in content


@pytest.mark.django_db()
def test_delete_asks_for_confirmation(channel_admin_client, widget_channel):
    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert 'hx-trigger="confirmed"' in content
    assert "confirmThenTrigger('delete-channel-button'" in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param('id="widget-tab-settings" aria-controls="widget-panel-settings"', id="settings-tab"),
        pytest.param('id="widget-tab-embed" aria-controls="widget-panel-embed"', id="embed-tab"),
        pytest.param('id="widget-panel-settings" aria-labelledby="widget-tab-settings"', id="settings-panel"),
        pytest.param('id="widget-panel-embed" aria-labelledby="widget-tab-embed"', id="embed-panel"),
        pytest.param('@keydown.right.prevent="focusTab(&#x27;embed&#x27;)"', id="arrow-to-embed"),
        pytest.param('@keydown.left.prevent="focusTab(&#x27;settings&#x27;)"', id="arrow-to-settings"),
    ],
)
def test_tabs_are_linked_to_their_panels(channel_admin_client, widget_channel, fragment):
    content = channel_admin_client.get(_edit_url(widget_channel)).content.decode()

    assert fragment in content


@pytest.mark.django_db()
@pytest.mark.parametrize("editing", [pytest.param(False, id="create"), pytest.param(True, id="edit")])
def test_domains_use_the_chip_editor(request, channel_admin_client, experiment, editing):
    url = _edit_url(request.getfixturevalue("widget_channel")) if editing else _create_url(experiment)

    content = channel_admin_client.get(url).content.decode()

    assert "Add domain" in content
    assert '<textarea name="allowed_domains" class="hidden"' in content
    assert 'x-model="draft" :disabled="allowAllDomains"' in content
    assert 'class="btn h-11 rounded-lg" :disabled="allowAllDomains" @click="add()"' in content
    assert ("Allow all domains" in content) is editing


@pytest.mark.django_db()
def test_edit_errors_are_linked_to_their_own_field(channel_admin_client, widget_channel):
    data = {
        "name": widget_channel.name,
        "platform": ChannelPlatform.EMBEDDED_WIDGET.value,
        "enabled": "on",
        "allow_all_domains": "on",
        "session_token_lifetime": "00:01:00",
    }

    content = channel_admin_client.post(_edit_url(widget_channel), data).content.decode()

    assert 'id="id_session_token_lifetime_error"' in content
    assert 'aria-describedby="id_session_token_lifetime_helptext id_session_token_lifetime_error"' in content
    assert 'aria-describedby="id_name_helptext"' in content
    assert 'id="id_name_error"' not in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("save", "label"), [pytest.param(False, "Cancel", id="unsaved"), pytest.param(True, "Close", id="saved")]
)
def test_footer_offers_close_after_a_save(channel_admin_client, widget_channel, save, label):
    if save:
        data = {
            "name": widget_channel.name,
            "platform": ChannelPlatform.EMBEDDED_WIDGET.value,
            "enabled": "on",
            "allow_all_domains": "on",
            "credential_mode": CredentialMode.EMBED_KEY.value,
        }
        response = channel_admin_client.post(_edit_url(widget_channel), data)
    else:
        response = channel_admin_client.get(_edit_url(widget_channel))

    assert f"onclick=\"this.closest('dialog').close()\">{label}</button>" in response.content.decode()
