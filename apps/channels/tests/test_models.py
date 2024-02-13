import pytest
from django.urls import reverse

from apps.channels.models import ExperimentChannel
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


def test_new_integration_does_not_raise_exception(db):
    channel = ExperimentChannelFactory()
    new_experiment = ExperimentFactory()

    ExperimentChannel.check_usage_by_another_experiment(
        channel.platform, identifier="321", new_experiment=new_experiment
    )


def test_duplicate_integration_raises_exception(db):
    channel = ExperimentChannelFactory()
    new_experiment = ExperimentFactory()

    with pytest.raises(ChannelAlreadyUtilizedException):
        ExperimentChannel.check_usage_by_another_experiment(
            channel.platform, identifier=channel.extra_data["bot_token"], new_experiment=new_experiment
        )


def test_channel_webhook_url(db):
    # Setup providers
    twilio_provider = MessagingProviderFactory(type=MessagingProviderType.twilio)
    turnio_provider = MessagingProviderFactory(type=MessagingProviderType.turnio)

    # Setup channels with their respective providers
    no_provider_channel = ExperimentChannelFactory()
    twilio_channel = ExperimentChannelFactory(messaging_provider=twilio_provider)
    turnio_channel = ExperimentChannelFactory(messaging_provider=turnio_provider)

    # Let's check out each one's webhook url
    assert no_provider_channel.webhook_url is None
    assert reverse("channels:new_twilio_message") in twilio_channel.webhook_url
    turnio_uri = reverse("channels:new_turn_message", kwargs={"experiment_id": turnio_channel.experiment.public_id})
    assert turnio_uri in turnio_channel.webhook_url
