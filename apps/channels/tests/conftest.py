import hashlib

import pytest

from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


@pytest.fixture()
def twilio_provider(db):
    return MessagingProviderFactory.create(
        name="twilio", type=MessagingProviderType.twilio, config={"auth_token": "123", "account_sid": "123"}
    )


@pytest.fixture()
def turn_io_provider():
    return MessagingProviderFactory.create(
        name="turnio", type=MessagingProviderType.turnio, config={"auth_token": "123"}
    )


@pytest.fixture()
def sureadhere_provider():
    return MessagingProviderFactory.create(
        name="sureadhere",
        type=MessagingProviderType.sureadhere,
        config={
            "client_id": "123",
            "client_secret": "456",
            "client_scope": "https://example.onmicrosoft.com/example-app-api/.default",
            "base_url": "https://example.com",
            "auth_url": "https://sa.b2clogin.com/sa.onmicrosoft.com/test_Patients/oauth2/v2.0/token",
        },
    )


@pytest.fixture()
def slack_provider():
    return MessagingProviderFactory.create(
        name="slack",
        type=MessagingProviderType.slack,
        config={
            "slack_channel_id": "CHANNEL_ID",
            "slack_installation_id": 1,
        },
    )


@pytest.fixture()
def meta_cloud_api_provider():
    verify_token = "test_verify_token"
    return MessagingProviderFactory.create(
        name="meta_cloud_api",
        type=MessagingProviderType.meta_cloud_api,
        config={
            "access_token": "test_token",
            "business_id": "biz_123",
            "app_secret": "test_app_secret",
            "verify_token": verify_token,
        },
        extra_data={
            "verify_token_hash": hashlib.sha256(verify_token.encode()).hexdigest(),
        },
    )


@pytest.fixture()
def turnio_whatsapp_channel(turn_io_provider):
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=turn_io_provider,
        experiment__team=turn_io_provider.team,
        extra_data={"number": "+14155238886"},
    )


@pytest.fixture()
def meta_cloud_api_whatsapp_channel(meta_cloud_api_provider):
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=meta_cloud_api_provider,
        experiment__team=meta_cloud_api_provider.team,
        extra_data={"number": "+15551234567", "phone_number_id": "12345"},
    )
