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
