from unittest.mock import MagicMock, patch

import pytest

from apps.channels.datamodels import TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import validate_twillio_request
from apps.service_providers.forms import TwilioMessagingConfigForm
from apps.service_providers.messaging_service import TwilioService

ACCOUNT = {"account_sid": "AC123", "auth_token": "TOKEN"}
API_KEY = {"api_key_sid": "SK123", "api_key_secret": "KEYSECRET"}


@pytest.mark.parametrize(
    ("config", "expected_args"),
    [
        pytest.param(ACCOUNT, ("AC123", "TOKEN"), id="auth-token"),
        pytest.param(ACCOUNT | {"api_key_sid": "", "api_key_secret": ""}, ("AC123", "TOKEN"), id="blank-api-key"),
        pytest.param(ACCOUNT | API_KEY, ("SK123", "KEYSECRET", "AC123"), id="api-key"),
    ],
)
def test_client_credentials(config, expected_args):
    with patch("twilio.rest.Client") as client_cls:
        _ = TwilioService(**config).client
    client_cls.assert_called_once_with(*expected_args)


@pytest.mark.parametrize(
    ("config", "expected_auth"),
    [
        pytest.param(ACCOUNT, ("AC123", "TOKEN"), id="auth-token"),
        pytest.param(ACCOUNT | API_KEY, ("SK123", "KEYSECRET"), id="api-key"),
    ],
)
def test_media_download_credentials(config, expected_auth):
    message = TwilioMessage(
        participant_id="x",
        message_text="",
        to="y",
        platform=ChannelPlatform.WHATSAPP,
        media_url="https://example.com/media",
    )
    response = MagicMock(content=b"data", headers={"Content-Type": "image/png"})
    with patch("httpx.get", return_value=response) as mock_get:
        TwilioService(**config).download_message_media(message)
    assert mock_get.call_args.kwargs["auth"] == expected_auth


def test_webhook_signature_uses_auth_token_when_api_key_is_set():
    channel = MagicMock()
    channel.messaging_provider.get_messaging_service.return_value = TwilioService(**ACCOUNT, **API_KEY)
    with patch("apps.channels.tasks.RequestValidator") as validator_cls:
        validate_twillio_request(channel, b"body", "request-uri", "signature")
    validator_cls.assert_called_once_with("TOKEN")


def test_api_key_secret_is_absent_from_the_repr():
    assert "KEYSECRET" not in repr(TwilioService(**ACCOUNT, **API_KEY))


@pytest.mark.parametrize(
    ("data", "error_field"),
    [
        pytest.param(ACCOUNT, None, id="auth-token-only"),
        pytest.param(ACCOUNT | API_KEY, None, id="with-api-key"),
        pytest.param(ACCOUNT | {"api_key_sid": "SK123"}, "api_key_secret", id="missing-secret"),
        pytest.param(ACCOUNT | {"api_key_secret": "KEYSECRET"}, "api_key_sid", id="missing-sid"),
        pytest.param(ACCOUNT | {"api_key_sid": "AC123", "api_key_secret": "KEYSECRET"}, "api_key_sid", id="not-a-key"),
        pytest.param({"account_sid": "AC123"} | API_KEY, "auth_token", id="auth-token-still-required"),
    ],
)
def test_form_validation(data, error_field):
    form = TwilioMessagingConfigForm(None, data=data)
    if error_field is None:
        assert form.is_valid(), form.errors
        TwilioService(**form.cleaned_data)
    else:
        assert not form.is_valid()
        assert list(form.errors) == [error_field]


def test_form_obfuscates_api_key_secret_and_keeps_it_on_resubmit():
    initial = ACCOUNT | {"api_key_sid": "SK123", "api_key_secret": "a-long-key-secret"}
    form = TwilioMessagingConfigForm(None, initial=initial)
    assert form.initial["api_key_secret"] == "a-lo...et"
    assert form.initial["api_key_sid"] == "SK123"

    resubmitted = TwilioMessagingConfigForm(None, data=form.initial, initial=initial)
    assert resubmitted.is_valid(), resubmitted.errors
    assert resubmitted.cleaned_data["api_key_secret"] == "a-long-key-secret"
