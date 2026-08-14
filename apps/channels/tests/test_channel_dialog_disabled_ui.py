"""The disable toggle and its indicators in the channel config UI (issue #4200)."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Permission
from django.template.loader import render_to_string
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


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
