from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from apps.channels.models import ChannelPlatform
from apps.service_providers.messaging_service import MetaCloudAPIService
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
        team=None,
        data={
            "auth_token": "test_key",
            "account_sid": "test_secret",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_messaging_provider_error(MessagingProviderType.twilio, data=form.cleaned_data)


@pytest.mark.parametrize(
    ("platform", "expected_provider_types"),
    [
        ("whatsapp", ["twilio", "turnio", "meta_cloud_api"]),
        ("telegram", []),
    ],
)
def test_platform_supported_platforms(platform: str, expected_provider_types: list):
    """Test that the correct services are being returned that supports a platform"""
    provider_types = MessagingProviderType.platform_supported_provider_types(platform=ChannelPlatform(platform))
    expected_provider_types = [MessagingProviderType(p_type) for p_type in expected_provider_types]
    assert provider_types == expected_provider_types


def _test_messaging_provider_error(provider_type: MessagingProviderType, data):
    form = provider_type.form_cls(None, data=data)
    assert not form.is_valid()

    with pytest.raises(ValidationError):
        provider_type.get_messaging_service(data)


@pytest.fixture()
def meta_cloud_api_service():
    return MetaCloudAPIService(access_token="test_token", business_id="123456")


def _mock_phone_numbers_response(data):
    return httpx.Response(200, json={"data": data}, request=httpx.Request("GET", "https://test"))


@pytest.mark.parametrize(
    ("api_data", "lookup_number", "expected_id"),
    [
        pytest.param(
            [
                {"id": "111", "display_phone_number": "+1 (212) 555-2368"},
                {"id": "222", "display_phone_number": "+27 81 234 5678"},
            ],
            "+12125552368",
            "111",
            id="formatted_number",
        ),
        pytest.param(
            [{"id": "333", "display_phone_number": "+27812345678"}],
            "+27812345678",
            "333",
            id="e164_number",
        ),
        pytest.param(
            [{"id": "111", "display_phone_number": "+1 212 555 2368"}],
            "+27812345678",
            None,
            id="no_match",
        ),
        pytest.param(
            [],
            "+12125552368",
            None,
            id="empty_response",
        ),
        pytest.param(
            [
                {"id": "111", "display_phone_number": "not-a-number"},
                {"id": "222", "display_phone_number": "+27 81 234 5678"},
            ],
            "+27812345678",
            "222",
            id="unparseable_number_skipped",
        ),
    ],
)
@patch("apps.service_providers.messaging_service.httpx.get")
def test_meta_cloud_api_get_phone_number_id(mock_get, meta_cloud_api_service, api_data, lookup_number, expected_id):
    mock_get.return_value = _mock_phone_numbers_response(api_data)
    assert meta_cloud_api_service.get_phone_number_id(lookup_number) == expected_id


def _test_messaging_provider(team, provider_type: MessagingProviderType, data):
    form = provider_type.form_cls(team, data=data)
    assert form.is_valid()
    MessagingProvider.objects.create(
        team=team,
        name=f"{provider_type} Test Provider",
        type=provider_type,
        config=form.cleaned_data,
    )
