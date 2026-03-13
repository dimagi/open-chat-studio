import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from apps.channels import meta_webhook
from apps.channels.models import ChannelPlatform
from apps.channels.views import MetaCloudAPIWebhookView
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

APP_SECRET = "test_app_secret"
VERIFY_TOKEN = "test_verify_token"


def _make_signature(payload: bytes, secret: str = APP_SECRET) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _meta_webhook_payload(phone_number_id="12345"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+15551234567",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
                            "messages": [
                                {
                                    "from": "27456897512",
                                    "id": "wamid.abc123",
                                    "timestamp": "1706709716",
                                    "text": {"body": "Hello"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


@pytest.fixture()
def meta_cloud_api_provider():
    return MessagingProviderFactory.create(
        type=MessagingProviderType.meta_cloud_api,
        config={
            "access_token": "test_token",
            "business_id": "biz_123",
            "app_secret": APP_SECRET,
            "verify_token": VERIFY_TOKEN,
        },
        extra_data={
            "verify_token_hash": hashlib.sha256(VERIFY_TOKEN.encode()).hexdigest(),
        },
    )


@pytest.fixture()
def meta_cloud_api_channel(meta_cloud_api_provider):
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=meta_cloud_api_provider,
        experiment__team=meta_cloud_api_provider.team,
        extra_data={"number": "+15551234567", "phone_number_id": "12345"},
    )


class TestMetaCloudAPIWebhookVerifySignature:
    def test_valid_signature(self):
        payload = b'{"test": "data"}'
        signature = _make_signature(payload)
        assert meta_webhook.verify_signature(payload, signature, APP_SECRET) is True

    def test_invalid_signature(self):
        payload = b'{"test": "data"}'
        assert meta_webhook.verify_signature(payload, "sha256=invalid", APP_SECRET) is False

    def test_missing_sha256_prefix(self):
        payload = b'{"test": "data"}'
        sig = hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        assert meta_webhook.verify_signature(payload, sig, APP_SECRET) is False

    def test_empty_app_secret(self):
        payload = b'{"test": "data"}'
        signature = _make_signature(payload)
        assert meta_webhook.verify_signature(payload, signature, "") is False


class TestMetaCloudAPIWebhookExtractMessageValues:
    def test_extracts_message_values(self):
        data = _meta_webhook_payload()
        values = meta_webhook.extract_message_values(data)
        assert len(values) == 1
        assert values[0]["metadata"]["phone_number_id"] == "12345"
        assert values[0]["messages"][0]["text"]["body"] == "Hello"


@pytest.mark.django_db()
class TestMetaCloudAPIWebhookVerifyWebhook:
    def test_valid_verification(self, meta_cloud_api_provider):
        """Simulate Meta's webhook verification process."""
        factory = RequestFactory()
        request = factory.get(
            "/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "challenge_string",
            },
        )
        response = meta_webhook.verify_webhook(request)
        assert response.status_code == 200
        assert response.content == b"challenge_string"

    def test_invalid_token(self, meta_cloud_api_provider):
        factory = RequestFactory()
        request = factory.get(
            "/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "challenge_string",
            },
        )
        response = meta_webhook.verify_webhook(request)
        assert response.status_code == 400


@pytest.mark.django_db()
class TestNewMetaCloudApiMessageGetVerification:
    """Test the GET verification flow through the view endpoint."""

    def test_valid_verification_via_view(self, meta_cloud_api_provider):
        factory = RequestFactory()
        request = factory.get(
            "/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "challenge_string",
            },
        )
        response = MetaCloudAPIWebhookView.as_view()(request)
        assert response.status_code == 200
        assert response.content == b"challenge_string"

    def test_invalid_token_via_view(self, meta_cloud_api_provider):
        factory = RequestFactory()
        request = factory.get(
            "/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "challenge_string",
            },
        )
        response = MetaCloudAPIWebhookView.as_view()(request)
        assert response.status_code == 400


@pytest.mark.django_db()
class TestNewMetaCloudApiMessage:
    def _post(self, payload_dict, app_secret=APP_SECRET):
        factory = RequestFactory()
        body = json.dumps(payload_dict).encode()
        signature = _make_signature(body, app_secret)
        request = factory.post(
            "/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        return MetaCloudAPIWebhookView.as_view()(request)

    @patch("apps.channels.tasks.handle_meta_cloud_api_message.delay")
    def test_valid_message_returns_200(self, mock_delay, meta_cloud_api_channel):
        response = self._post(_meta_webhook_payload())
        assert response.status_code == 200
        mock_delay.assert_called_once()

    @patch("apps.channels.tasks.handle_meta_cloud_api_message.delay")
    def test_task_dispatched_with_correct_args(self, mock_delay, meta_cloud_api_channel):
        self._post(_meta_webhook_payload())
        mock_delay.assert_called_once_with(
            channel_id=meta_cloud_api_channel.id,
            team_slug=meta_cloud_api_channel.team.slug,
            message_data=_meta_webhook_payload()["entry"][0]["changes"][0]["value"],
        )

    def test_invalid_signature_returns_200(self, meta_cloud_api_channel):
        """Invalid signature returns 200 to prevent Meta from retrying."""
        factory = RequestFactory()
        body = json.dumps(_meta_webhook_payload()).encode()
        request = factory.post(
            "/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=invalid",
        )
        response = MetaCloudAPIWebhookView.as_view()(request)
        assert response.status_code == 200

    def test_missing_signature_header_returns_200(self, meta_cloud_api_channel):
        """Missing signature header returns 200 to prevent Meta from retrying."""
        factory = RequestFactory()
        body = json.dumps(_meta_webhook_payload()).encode()
        request = factory.post(
            "/",
            data=body,
            content_type="application/json",
        )
        response = MetaCloudAPIWebhookView.as_view()(request)
        assert response.status_code == 200

    @patch("apps.channels.tasks.handle_meta_cloud_api_message.delay")
    def test_multi_phone_number_payload_routes_to_correct_channels(
        self, mock_delay, meta_cloud_api_channel, meta_cloud_api_provider
    ):
        """Messages for different phone numbers must be routed to their own channels, not all to the first."""
        # Both channels share the same provider (same app, same app_secret) — only the phone number differs.
        second_channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider=meta_cloud_api_provider,
            experiment__team=meta_cloud_api_provider.team,
            extra_data={"number": "+15559999999", "phone_number_id": "99999"},
        )

        # Build a payload with two entries for two different phone numbers
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "BIZ_ID",
                    "changes": [
                        {
                            "value": _meta_webhook_payload("12345")["entry"][0]["changes"][0]["value"],
                            "field": "messages",
                        },
                        {
                            "value": _meta_webhook_payload("99999")["entry"][0]["changes"][0]["value"],
                            "field": "messages",
                        },
                    ],
                }
            ],
        }
        response = self._post(payload)
        assert response.status_code == 200
        assert mock_delay.call_count == 2
        called_channel_ids = {call.kwargs["channel_id"] for call in mock_delay.call_args_list}
        assert called_channel_ids == {meta_cloud_api_channel.id, second_channel.id}

    @patch("apps.channels.tasks.handle_meta_cloud_api_message.delay")
    def test_unknown_phone_number_in_multi_payload_skipped(self, mock_delay, meta_cloud_api_channel):
        """A value with an unknown phone_number_id is skipped; other values are still dispatched."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "BIZ_ID",
                    "changes": [
                        {
                            "value": _meta_webhook_payload("12345")["entry"][0]["changes"][0]["value"],
                            "field": "messages",
                        },
                        {
                            "value": _meta_webhook_payload("unknown_id")["entry"][0]["changes"][0]["value"],
                            "field": "messages",
                        },
                    ],
                }
            ],
        }
        response = self._post(payload)
        assert response.status_code == 200
        assert mock_delay.call_count == 1
        mock_delay.assert_called_once_with(
            channel_id=meta_cloud_api_channel.id,
            team_slug=meta_cloud_api_channel.team.slug,
            message_data=_meta_webhook_payload("12345")["entry"][0]["changes"][0]["value"],
        )

    def test_invalid_json_returns_400(self):
        factory = RequestFactory()
        body = b"not json"
        signature = _make_signature(body)
        request = factory.post(
            "/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        response = MetaCloudAPIWebhookView.as_view()(request)
        assert response.status_code == 400
