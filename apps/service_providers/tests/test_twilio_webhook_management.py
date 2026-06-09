from unittest.mock import MagicMock, patch

import pytest

from apps.service_providers.messaging_service import MetaCloudAPIService, TwilioService


class TestTwilioWebhookManagement:
    WEBHOOK_URL = "https://example.com/channels/twilio/incoming_message"

    def _make_sender(self, callback_url=None):
        sender = MagicMock()
        sender.sid = "XE1"
        sender.sender_id = "whatsapp:+27812345678"
        sender.webhook = {
            "callback_url": callback_url,
            "callback_method": "POST" if callback_url else None,
            "fallback_url": None,
            "fallback_method": None,
            "status_callback_url": "https://example.com/status",
            "status_callback_method": "POST",
        }
        return sender

    def _service_with_client(self, senders):
        service = TwilioService(account_sid="test", auth_token="test")
        client = MagicMock()
        client.messaging.v2.channels_senders.list.return_value = senders
        return service, client

    def test_supports_webhook_management(self):
        assert TwilioService(account_sid="test", auth_token="test").supports_webhook_management
        assert not MetaCloudAPIService(access_token="test", business_id="123").supports_webhook_management

    def test_set_incoming_webhook_updates_matching_sender(self):
        sender = self._make_sender(callback_url="https://old.example.com/hook")
        service, client = self._service_with_client([sender])
        with patch.object(TwilioService, "client", new=client):
            service.set_incoming_webhook({"number": "+27812345678"}, self.WEBHOOK_URL)

        client.messaging.v2.channels_senders.list.assert_called_once_with(channel="whatsapp")
        client.messaging.v2.channels_senders.assert_called_once_with("XE1")
        update_mock = client.messaging.v2.channels_senders.return_value.update
        update_mock.assert_called_once()
        payload = update_mock.call_args.kwargs["messaging_v2_channels_sender_requests_update"]
        assert payload.to_dict() == {
            "webhook": {
                "callback_url": self.WEBHOOK_URL,
                "callback_method": "POST",
                "status_callback_url": "https://example.com/status",
                "status_callback_method": "POST",
            }
        }

    def test_set_incoming_webhook_raises_when_sender_not_found(self):
        service, client = self._service_with_client([])
        with patch.object(TwilioService, "client", new=client), pytest.raises(ValueError, match="No WhatsApp sender"):
            service.set_incoming_webhook({"number": "+27812345678"}, self.WEBHOOK_URL)

    def test_remove_incoming_webhook_clears_matching_url(self):
        sender = self._make_sender(callback_url=self.WEBHOOK_URL)
        service, client = self._service_with_client([sender])
        with patch.object(TwilioService, "client", new=client):
            service.remove_incoming_webhook({"number": "+27812345678"}, self.WEBHOOK_URL)

        update_mock = client.messaging.v2.channels_senders.return_value.update
        update_mock.assert_called_once()
        payload = update_mock.call_args.kwargs["messaging_v2_channels_sender_requests_update"]
        assert payload.to_dict()["webhook"]["callback_url"] == ""

    def test_remove_incoming_webhook_ignores_repointed_sender(self):
        sender = self._make_sender(callback_url="https://somewhere-else.example.com/hook")
        service, client = self._service_with_client([sender])
        with patch.object(TwilioService, "client", new=client):
            service.remove_incoming_webhook({"number": "+27812345678"}, self.WEBHOOK_URL)

        client.messaging.v2.channels_senders.return_value.update.assert_not_called()

    def test_remove_incoming_webhook_ignores_missing_sender(self):
        service, client = self._service_with_client([])
        with patch.object(TwilioService, "client", new=client):
            service.remove_incoming_webhook({"number": "+27812345678"}, self.WEBHOOK_URL)

        client.messaging.v2.channels_senders.return_value.update.assert_not_called()

    def test_remove_incoming_webhook_ignores_missing_number(self):
        service, client = self._service_with_client([])
        with patch.object(TwilioService, "client", new=client):
            service.remove_incoming_webhook({}, self.WEBHOOK_URL)

        client.messaging.v2.channels_senders.list.assert_not_called()
