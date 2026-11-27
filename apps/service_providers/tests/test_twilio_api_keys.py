from unittest.mock import MagicMock, patch

import pydantic
import pytest
from twilio.request_validator import RequestValidator

from apps.channels.datamodels import TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import validate_twillio_request
from apps.service_providers.forms import TwilioMessagingConfigForm
from apps.service_providers.messaging_service import TwilioService

ACCOUNT = {"account_sid": "AC123", "auth_token": "TOKEN"}
API_KEY = {"api_key_sid": "SK123", "api_key_secret": "KEYSECRET"}
SHARED_KEY_ID = {"webhook_shared_key_id": "webhooks_sharedkey_01"}
SHARED_KEY = SHARED_KEY_ID | {"webhook_shared_key_secret": "SHAREDSECRET"}
NO_AUTH_TOKEN = {"account_sid": "AC123"} | API_KEY | SHARED_KEY


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


URI = "https://example.com/channels/twilio/incoming_message"
PARAMS = {"Body": "hi", "From": "whatsapp:+123"}


def _validate(config, signed_with, algorithm=None, header_algorithm=None):
    """Validate a webhook signed with `signed_with`; a shared-key signature names its algorithm in a header."""
    channel = MagicMock()
    channel.messaging_provider.get_messaging_service.return_value = TwilioService(**config)
    signature = RequestValidator(signed_with).compute_signature(URI, PARAMS, algorithm)
    header_algorithm = header_algorithm or algorithm
    header = f"key=webhooks_sharedkey_01,algorithm={header_algorithm}" if header_algorithm else None
    return validate_twillio_request(channel, PARAMS, URI, signature, signature_settings=header)


@pytest.mark.parametrize(
    ("config", "signed_with", "algorithm", "expected"),
    [
        pytest.param(ACCOUNT, "TOKEN", None, True, id="auth-token"),
        pytest.param(ACCOUNT | API_KEY, "TOKEN", None, True, id="auth-token-with-api-key"),
        pytest.param(ACCOUNT | API_KEY, "KEYSECRET", None, False, id="api-key-secret-does-not-sign"),
        pytest.param(NO_AUTH_TOKEN, "SHAREDSECRET", "HMAC_SHA256", True, id="shared-key-sha256"),
        pytest.param(NO_AUTH_TOKEN, "SHAREDSECRET", "HMAC_SHA1", True, id="shared-key-sha1"),
        pytest.param(NO_AUTH_TOKEN, "WRONG", "HMAC_SHA256", False, id="wrong-shared-secret"),
        pytest.param(ACCOUNT | SHARED_KEY, "SHAREDSECRET", "HMAC_SHA256", True, id="both-set-shared-key-signed"),
        pytest.param(ACCOUNT | SHARED_KEY, "TOKEN", None, True, id="both-set-auth-token-signed"),
        pytest.param(ACCOUNT | SHARED_KEY, "TOKEN", "HMAC_SHA256", False, id="auth-token-cannot-sign-as-shared-key"),
        pytest.param(ACCOUNT | SHARED_KEY, "SHAREDSECRET", None, False, id="shared-key-cannot-sign-as-auth-token"),
        pytest.param(ACCOUNT, "SHAREDSECRET", "HMAC_SHA256", False, id="shared-key-not-configured"),
        pytest.param(NO_AUTH_TOKEN, "TOKEN", None, False, id="auth-token-not-configured"),
    ],
)
def test_webhook_signature_validation(config, signed_with, algorithm, expected):
    assert _validate(config, signed_with, algorithm) is expected


def test_webhook_signature_with_unsupported_algorithm_is_rejected():
    assert _validate(NO_AUTH_TOKEN, "SHAREDSECRET", "HMAC_SHA256", header_algorithm="HMAC_MD5") is False


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"account_sid": "AC123"}, id="no-credentials"),
        pytest.param({"account_sid": "AC123"} | API_KEY, id="api-key-only"),
        pytest.param({"account_sid": "AC123"} | SHARED_KEY, id="shared-key-only"),
        pytest.param(NO_AUTH_TOKEN | {"webhook_shared_key_id": ""}, id="shared-key-without-id"),
    ],
)
def test_service_requires_auth_token_or_both_keys(config):
    with pytest.raises(pydantic.ValidationError):
        TwilioService(**config)


def test_service_without_auth_token_uses_api_key():
    with patch("twilio.rest.Client") as client_cls:
        _ = TwilioService(**NO_AUTH_TOKEN).client
    client_cls.assert_called_once_with("SK123", "KEYSECRET", "AC123")


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
        pytest.param({"account_sid": "AC123"} | API_KEY, "auth_token", id="api-key-only"),
        pytest.param({"account_sid": "AC123"} | SHARED_KEY, "auth_token", id="shared-key-only"),
        pytest.param({"account_sid": "AC123"}, "auth_token", id="no-credentials"),
        pytest.param(NO_AUTH_TOKEN, None, id="both-keys-without-auth-token"),
        pytest.param(ACCOUNT | SHARED_KEY, None, id="auth-token-with-shared-key"),
        pytest.param(ACCOUNT | SHARED_KEY_ID, "webhook_shared_key_secret", id="missing-shared-secret"),
        pytest.param(ACCOUNT | {"webhook_shared_key_secret": "S"}, "webhook_shared_key_id", id="missing-shared-key-id"),
        pytest.param(
            ACCOUNT | SHARED_KEY | {"webhook_shared_key_id": "SK123"}, "webhook_shared_key_id", id="not-a-shared-key"
        ),
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


def test_editing_a_provider_without_an_api_key_keeps_it_usable():
    form = TwilioMessagingConfigForm(None, initial=ACCOUNT)
    resubmitted = TwilioMessagingConfigForm(
        None, data=form.initial | dict.fromkeys([*API_KEY, *SHARED_KEY], ""), initial=ACCOUNT
    )
    assert resubmitted.is_valid(), resubmitted.errors
    assert resubmitted.cleaned_data["api_key_secret"] == ""
    assert resubmitted.cleaned_data["webhook_shared_key_secret"] == ""
    assert TwilioService(**resubmitted.cleaned_data).auth_token.get_secret_value() == "TOKEN"
