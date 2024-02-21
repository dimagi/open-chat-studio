import pytest
from pydantic import ValidationError

from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProvider, MessagingProviderType


def test_twilio_messaging_provider(team_with_users):
    _test_messaging_provider(
        team_with_users,
        MessagingProviderType.twilio,
        data={
            "auth_token": "test_token",
            "account_sid": "account_sid",
        },
    )


@pytest.mark.parametrize(
    "config_key",
    [
        "auth_token",
        "account_sid",
    ],
)
def test_twilio_messaging_provider_error(config_key):
    """Test that any missing param causes failure"""
    form = MessagingProviderType.twilio.form_cls(
        data={
            "auth_token": "test_key",
            "account_sid": "test_secret",
        }
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_messaging_provider_error(MessagingProviderType.twilio, data=form.cleaned_data)


@pytest.mark.parametrize(
    ("platform", "expected_provider_types"),
    [
        ("whatsapp", ["twilio", "turnio"]),
        ("telegram", []),
    ],
)
def test_platform_supported_platforms(platform: str, expected_provider_types: list):
    """Test that the correct services are being returned that supports a platform"""
    provider_types = MessagingProviderType.platform_supported_provider_types(platform=ChannelPlatform(platform))
    expected_provider_types = [MessagingProviderType(p_type) for p_type in expected_provider_types]
    assert provider_types == expected_provider_types


def _test_messaging_provider_error(provider_type: MessagingProviderType, data):
    form = provider_type.form_cls(data=data)
    assert not form.is_valid()

    with pytest.raises(ValidationError):
        provider_type.get_messaging_service(data)


def _test_messaging_provider(team, provider_type: MessagingProviderType, data):
    form = provider_type.form_cls(data=data)
    assert form.is_valid()
    MessagingProvider.objects.create(
        team=team,
        name=f"{provider_type} Test Provider",
        type=provider_type,
        config=form.cleaned_data,
    )
