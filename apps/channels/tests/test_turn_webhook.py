import base64
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels import turn_webhook
from apps.channels.tests.message_examples import turnio_messages

HMAC_SECRET = "turn_test_hmac_secret"


def _sign(payload: bytes, secret: str = HMAC_SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode()


class TestVerifySignature:
    def test_valid_signature(self):
        payload = b'{"messages": [{"id": "abc"}]}'
        assert turn_webhook.verify_signature(payload, _sign(payload), HMAC_SECRET) is True

    def test_signature_from_a_different_secret(self):
        payload = b'{"messages": [{"id": "abc"}]}'
        assert turn_webhook.verify_signature(payload, _sign(payload, "other_secret"), HMAC_SECRET) is False

    def test_tampered_body(self):
        signature = _sign(b'{"messages": [{"id": "abc"}]}')
        assert turn_webhook.verify_signature(b'{"messages": [{"id": "xyz"}]}', signature, HMAC_SECRET) is False

    def test_reserialized_body_does_not_verify(self):
        """Raw-body discipline: re-serializing reorders keys, so only request.body may be signed."""
        raw = b'{"b": 2, "a": 1}'
        reserialized = json.dumps(json.loads(raw), sort_keys=True).encode()
        assert turn_webhook.verify_signature(reserialized, _sign(raw), HMAC_SECRET) is False

    @pytest.mark.parametrize(
        "signature_header",
        [
            pytest.param("", id="empty_header"),
            pytest.param("not-base64-at-all", id="garbage_header"),
            pytest.param("sha256=" + _sign(b'{"messages": []}'), id="prefixed_header"),
            pytest.param("sïgnature-with-non-ascii", id="non_ascii_header"),
        ],
    )
    def test_unusable_header_returns_false_without_raising(self, signature_header):
        assert turn_webhook.verify_signature(b'{"messages": []}', signature_header, HMAC_SECRET) is False

    def test_empty_secret(self):
        payload = b'{"messages": []}'
        assert turn_webhook.verify_signature(payload, _sign(payload), "") is False

    def test_empty_payload_with_matching_signature(self):
        assert turn_webhook.verify_signature(b"", _sign(b""), HMAC_SECRET) is True


def _post(client, channel, payload: dict, signature: str | None = None):
    body = json.dumps(payload).encode()
    headers = {}
    if signature is not None:
        headers["HTTP_X_TURN_HOOK_SIGNATURE"] = signature
    return client.post(
        reverse("channels:new_turn_message", kwargs={"experiment_id": channel.experiment.public_id}),
        data=body,
        content_type="application/json",
        **headers,
    )


@pytest.fixture()
def signed_turn_channel(turnio_whatsapp_channel):
    provider = turnio_whatsapp_channel.messaging_provider
    provider.config = {"auth_token": "123", "hmac_secret": HMAC_SECRET}
    provider.save()
    return turnio_whatsapp_channel


@pytest.mark.django_db()
class TestNewTurnMessageSignatureVerification:
    @patch("apps.channels.tasks.handle_turn_message")
    def test_unconfigured_provider_still_accepts_unsigned_webhooks(self, task, client, turnio_whatsapp_channel):
        """Staged rollout: providers that have not copied their secret across keep working."""
        response = _post(client, turnio_whatsapp_channel, turnio_messages.text_message())
        assert response.status_code == 200
        task.delay.assert_called_once()

    @patch("apps.channels.tasks.handle_turn_message")
    def test_valid_signature_is_accepted(self, task, client, signed_turn_channel):
        payload = turnio_messages.text_message()
        signature = _sign(json.dumps(payload).encode())
        response = _post(client, signed_turn_channel, payload, signature)
        assert response.status_code == 200
        task.delay.assert_called_once()

    @patch("apps.channels.tasks.handle_turn_message")
    def test_invalid_signature_is_rejected(self, task, client, signed_turn_channel):
        response = _post(client, signed_turn_channel, turnio_messages.text_message(), "bm90LWEtc2lnbmF0dXJl")
        assert response.status_code == 401
        task.delay.assert_not_called()

    @patch("apps.channels.tasks.handle_turn_message")
    def test_missing_signature_header_is_rejected(self, task, client, signed_turn_channel):
        response = _post(client, signed_turn_channel, turnio_messages.text_message())
        assert response.status_code == 401
        task.delay.assert_not_called()

    @patch("apps.channels.tasks.handle_turn_message")
    def test_signature_over_a_different_body_is_rejected(self, task, client, signed_turn_channel):
        signature = _sign(json.dumps(turnio_messages.text_message()).encode())
        other_payload = turnio_messages.text_message()
        other_payload["messages"][0]["text"]["body"] = "tampered"
        response = _post(client, signed_turn_channel, other_payload, signature)
        assert response.status_code == 401
        task.delay.assert_not_called()

    @pytest.mark.parametrize(
        "stored_secret",
        [
            pytest.param("   ", id="whitespace_only"),
            pytest.param("", id="empty_string"),
            pytest.param(None, id="none"),
        ],
    )
    @patch("apps.channels.tasks.handle_turn_message")
    def test_blank_stored_secret_counts_as_unconfigured(self, task, stored_secret, client, turnio_whatsapp_channel):
        provider = turnio_whatsapp_channel.messaging_provider
        provider.config = {"auth_token": "123", "hmac_secret": stored_secret}
        provider.save()
        response = _post(client, turnio_whatsapp_channel, turnio_messages.text_message())
        assert response.status_code == 200
        task.delay.assert_called_once()

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(turnio_messages.status_message(), id="status_callback_no_messages_key"),
            pytest.param(turnio_messages.system_user_changed_number_message(), id="non_conversational"),
        ],
    )
    @patch("apps.channels.tasks.handle_turn_message")
    def test_ignored_payloads_are_filtered_before_verification(self, task, payload, client, signed_turn_channel):
        """Turn's status callbacks and system messages are filtered out before the signature
        check, so an unsigned one gets a 200 rather than a 401 in the customer's Turn dashboard.
        `status_message` has no "messages" key; `system_user_changed_number_message` has one and
        is caught by is_non_conversational_whatsapp_message, the two filters run in that order."""
        response = _post(client, signed_turn_channel, payload)
        assert response.status_code == 200
        task.delay.assert_not_called()


@pytest.mark.django_db()
class TestNewTurnMessageRequestHandling:
    def test_get_returns_405(self, client, turnio_whatsapp_channel):
        """Previously a GET reached json.loads on an empty body and returned a 500."""
        url = reverse(
            "channels:new_turn_message", kwargs={"experiment_id": turnio_whatsapp_channel.experiment.public_id}
        )
        assert client.get(url).status_code == 405

    def test_malformed_body_returns_400(self, client, turnio_whatsapp_channel):
        url = reverse(
            "channels:new_turn_message", kwargs={"experiment_id": turnio_whatsapp_channel.experiment.public_id}
        )
        response = client.post(url, data=b"not json", content_type="application/json")
        assert response.status_code == 400
