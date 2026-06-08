from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


@pytest.fixture()
def whatsapp_channel(team_with_users):
    provider = MessagingProviderFactory(
        team=team_with_users,
        type=MessagingProviderType.twilio,
        config={"account_sid": "123", "auth_token": "123"},
    )
    experiment = ExperimentFactory(team=team_with_users)
    return ExperimentChannelFactory(
        team=team_with_users,
        experiment=experiment,
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=provider,
        extra_data={"number": "+12125552368"},
    )


def _delete_channel(client, team, channel):
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="delete_experimentchannel"))
    client.force_login(user)
    url = reverse("channels:delete_channel", args=[team.slug, channel.experiment.id, channel.id])
    return client.post(url)


@pytest.mark.django_db()
@patch("apps.service_providers.messaging_service.TwilioService.remove_incoming_webhook")
def test_delete_channel_clears_remote_webhook(remove_incoming_webhook, client, team_with_users, whatsapp_channel):
    response = _delete_channel(client, team_with_users, whatsapp_channel)

    assert response.status_code == 200
    whatsapp_channel.refresh_from_db()
    assert whatsapp_channel.deleted
    remove_incoming_webhook.assert_called_once_with(whatsapp_channel.extra_data, whatsapp_channel.webhook_url)


@pytest.mark.django_db()
@patch("apps.service_providers.messaging_service.TwilioService.remove_incoming_webhook")
def test_delete_channel_succeeds_when_webhook_removal_fails(
    remove_incoming_webhook, client, team_with_users, whatsapp_channel
):
    remove_incoming_webhook.side_effect = Exception("Twilio is down")

    response = _delete_channel(client, team_with_users, whatsapp_channel)

    assert response.status_code == 200
    whatsapp_channel.refresh_from_db()
    assert whatsapp_channel.deleted
