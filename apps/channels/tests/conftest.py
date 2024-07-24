import pytest

from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


@pytest.fixture()
def twilio_provider(db):
    return MessagingProviderFactory(
        name="twilio", type=MessagingProviderType.twilio, config={"auth_token": "123", "account_sid": "123"}
    )


@pytest.fixture()
def turn_io_provider():
    return MessagingProviderFactory(name="turnio", type=MessagingProviderType.turnio, config={"auth_token": "123"})


@pytest.fixture()
def sureadhere_provider():
    return MessagingProviderFactory(
        name="sureadhere",
        type=MessagingProviderType.sureadhere,
        config={"client_id": "123", "client_secret": "456", "base_url": "https://example.com"},
    )


@pytest.fixture()
def slack_provider():
    return MessagingProviderFactory(
        name="slack",
        type=MessagingProviderType.slack,
        config={
            "slack_channel_id": "CHANNEL_ID",
            "slack_installation_id": 1,
        },
    )
