"""The disable toggle and its indicators in the channel config UI (issue #4200)."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Permission
from django.template.loader import render_to_string
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


@pytest.fixture()
def telegram_channel(team_with_users):
    experiment = ExperimentFactory(team=team_with_users)
    return ExperimentChannelFactory(
        team=team_with_users,
        experiment=experiment,
        platform=ChannelPlatform.TELEGRAM,
        extra_data={"bot_token": "tok"},
    )


def _open_edit_dialog(client, team, channel):
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="change_experimentchannel"))
    client.force_login(user)
    url = reverse("channels:channel_edit_dialog", args=[team.slug, channel.experiment.id, channel.id])
    return client.get(url)


@pytest.mark.django_db()
def test_edit_dialog_offers_the_disable_toggle(client, team_with_users, telegram_channel):
    response = _open_edit_dialog(client, team_with_users, telegram_channel)

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="enabled"' in content
    assert 'name="disabled_message"' in content
    assert "This channel is disabled" not in content


@pytest.mark.django_db()
def test_edit_dialog_warns_when_the_channel_is_disabled(client, team_with_users, telegram_channel):
    telegram_channel.enabled = False
    telegram_channel.disabled_message = "Back on Monday"
    telegram_channel.save()

    response = _open_edit_dialog(client, team_with_users, telegram_channel)

    content = response.content.decode()
    assert "This channel is disabled" in content
    assert "users receive the message below" in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("enabled", "expect_badge"),
    [
        pytest.param(True, False, id="enabled_channel_has_no_badge"),
        pytest.param(False, True, id="disabled_channel_is_badged"),
    ],
)
def test_channel_list_highlights_disabled_channels(telegram_channel, enabled, expect_badge):
    telegram_channel.enabled = enabled
    telegram_channel.save()

    html = render_to_string(
        "chatbots/components/channel_buttons.html",
        {
            "channels": [telegram_channel],
            "experiment": telegram_channel.experiment,
            "platforms": {},
            "request": SimpleNamespace(team=telegram_channel.team),
        },
    )

    assert ("badge-warning" in html) is expect_badge
    assert ("Disabled" in html) is expect_badge


@pytest.fixture()
def whatsapp_channel(team_with_users):
    """A channel on a Meta provider whose numbers have been synced."""
    provider = MessagingProviderFactory(
        team=team_with_users,
        type=MessagingProviderType.meta_cloud_api,
        config={"access_token": "token", "business_id": "biz"},
        extra_data={
            "whatsapp_numbers": {
                "state": "ok",
                "numbers": [
                    {
                        "phone_number_id": "111",
                        "number": "+27647084804",
                        "display": "+27 64 708 4804",
                        "verified_name": "TenantHive",
                    }
                ],
            }
        },
    )
    experiment = ExperimentFactory(team=team_with_users)
    return ExperimentChannelFactory(
        team=team_with_users,
        experiment=experiment,
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=provider,
        extra_data={"number": "+27647084804", "phone_number_id": "111"},
    )


@pytest.mark.django_db()
def test_whatsapp_dialog_renders_both_number_controls(client, team_with_users, whatsapp_channel):
    """Both controls ship with the page so switching provider needs no request."""
    response = _open_edit_dialog(client, team_with_users, whatsapp_channel)

    content = response.content.decode()
    assert response.status_code == 200
    assert '<select class="select w-full"' in content
    assert 'id="id_number_free"' in content
    assert "numbersByProvider" in content
    assert "+27 64 708 4804 - TenantHive" in content
