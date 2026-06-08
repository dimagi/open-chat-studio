from io import StringIO
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from django.core.management import call_command
from twilio.base.exceptions import TwilioException

from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

OLD_DOMAIN = "chatbots.dimagi.com"
NEW_DOMAIN = "www.openchatstudio.com"


def _sender(sid, callback_url=None, status_callback_url=None):
    sender = MagicMock()
    sender.sid = sid
    sender.sender_id = "whatsapp:+15551234567"
    sender.webhook = {
        "callback_url": callback_url,
        "callback_method": "POST",
        "fallback_url": None,
        "fallback_method": None,
        "status_callback_url": status_callback_url,
        "status_callback_method": "POST" if status_callback_url else None,
    }
    return sender


def _service(sid, inbound_request_url=None, fallback_url=None, status_callback=None):
    service = MagicMock()
    service.sid = sid
    service.friendly_name = "Test Service"
    service.inbound_request_url = inbound_request_url
    service.fallback_url = fallback_url
    service.status_callback = status_callback
    return service


@pytest.fixture()
def twilio_client():
    MessagingProviderFactory(
        type=MessagingProviderType.twilio,
        config={"account_sid": "AC123", "auth_token": "token"},
    )
    with patch(
        "apps.service_providers.messaging_service.TwilioService.client", new_callable=PropertyMock
    ) as mock_client:
        client = MagicMock()
        client.messaging.v2.channels_senders.list.return_value = []
        client.messaging.v1.services.list.return_value = []
        mock_client.return_value = client
        yield client


def _call_command(*args):
    out = StringIO()
    call_command("migrate_twilio_webhooks", "--new-domain", NEW_DOMAIN, *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db()
def test_dry_run_reports_but_does_not_update(twilio_client):
    sender = _sender("XE1", callback_url=f"https://{OLD_DOMAIN}/channels/whatsapp/incoming_message")
    service = _service("MG1", inbound_request_url=f"https://{OLD_DOMAIN}/channels/twilio/incoming_message")
    twilio_client.messaging.v2.channels_senders.list.return_value = [sender]
    twilio_client.messaging.v1.services.list.return_value = [service]

    output = _call_command()

    # the domain is migrated and the legacy whatsapp path is normalized
    assert output.count(f"-> https://{NEW_DOMAIN}/channels/twilio/incoming_message") == 2
    twilio_client.messaging.v2.channels_senders.return_value.update.assert_not_called()
    service.update.assert_not_called()


@pytest.mark.django_db()
def test_apply_updates_sender_webhooks(twilio_client):
    sender = _sender(
        "XE1",
        callback_url=f"https://{OLD_DOMAIN}/channels/whatsapp/incoming_message",
        status_callback_url=f"https://{OLD_DOMAIN}/channels/twilio/status",
    )
    twilio_client.messaging.v2.channels_senders.list.return_value = [sender]

    _call_command("--apply")

    twilio_client.messaging.v2.channels_senders.list.assert_called_once_with(channel="whatsapp")
    twilio_client.messaging.v2.channels_senders.assert_called_once_with("XE1")
    update_mock = twilio_client.messaging.v2.channels_senders.return_value.update
    update_mock.assert_called_once()
    payload = update_mock.call_args.kwargs["messaging_v2_channels_sender_requests_update"]
    assert payload.to_dict() == {
        "webhook": {
            "callback_url": f"https://{NEW_DOMAIN}/channels/twilio/incoming_message",
            "callback_method": "POST",
            "status_callback_url": f"https://{NEW_DOMAIN}/channels/twilio/status",
            "status_callback_method": "POST",
        }
    }


@pytest.mark.django_db()
def test_legacy_paths_on_the_new_domain_are_normalized(twilio_client):
    sender = _sender("XE1", callback_url=f"https://{NEW_DOMAIN}/channels/whatsapp/incoming_message")
    twilio_client.messaging.v2.channels_senders.list.return_value = [sender]

    _call_command("--apply")

    update_mock = twilio_client.messaging.v2.channels_senders.return_value.update
    update_mock.assert_called_once()
    payload = update_mock.call_args.kwargs["messaging_v2_channels_sender_requests_update"]
    assert payload.to_dict()["webhook"]["callback_url"] == f"https://{NEW_DOMAIN}/channels/twilio/incoming_message"


@pytest.mark.django_db()
def test_apply_updates_messaging_service_urls(twilio_client):
    service = _service(
        "MG1",
        inbound_request_url=f"https://{OLD_DOMAIN}/channels/twilio/incoming_message",
        fallback_url="https://example.com/fallback",
    )
    twilio_client.messaging.v1.services.list.return_value = [service]

    _call_command("--apply")

    service.update.assert_called_once_with(inbound_request_url=f"https://{NEW_DOMAIN}/channels/twilio/incoming_message")


@pytest.mark.django_db()
def test_senders_and_services_seen_under_multiple_providers_are_only_updated_once(twilio_client):
    # two providers configured with the same Twilio account will list the same senders and services
    MessagingProviderFactory(
        type=MessagingProviderType.twilio,
        config={"account_sid": "AC123", "auth_token": "token"},
    )
    sender = _sender("XE1", callback_url=f"https://{OLD_DOMAIN}/channels/whatsapp/incoming_message")
    service = _service("MG1", inbound_request_url=f"https://{OLD_DOMAIN}/channels/twilio/incoming_message")
    twilio_client.messaging.v2.channels_senders.list.return_value = [sender]
    twilio_client.messaging.v1.services.list.return_value = [service]

    _call_command("--apply")

    twilio_client.messaging.v2.channels_senders.return_value.update.assert_called_once()
    service.update.assert_called_once()


@pytest.mark.django_db()
def test_twilio_errors_do_not_abort_the_run(twilio_client):
    twilio_client.messaging.v2.channels_senders.list.side_effect = TwilioException("Unable to fetch page")

    _call_command()  # should not raise

    twilio_client.messaging.v1.services.list.assert_not_called()


@pytest.mark.django_db()
def test_urls_on_other_domains_are_not_touched(twilio_client):
    sender = _sender("XE1", callback_url="https://example.com/channels/whatsapp/incoming_message")
    twilio_client.messaging.v2.channels_senders.list.return_value = [sender]

    _call_command("--apply")

    twilio_client.messaging.v2.channels_senders.return_value.update.assert_not_called()
