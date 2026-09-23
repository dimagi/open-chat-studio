from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.channels.facebook_channel import (
    FacebookMessengerChannel,
)
from apps.channels.tests.channels.conftest import make_context

PAGE_ID = "page123"
RECIPIENT = "27456897512"
LAST_ACTIVITY = datetime(2026, 7, 1, 12, 0)


def _bound_context():
    session = SimpleNamespace(last_activity_at=LAST_ACTIVITY)
    return make_context(experiment_session=session)


def _make_channel(voice_replies_supported=True):
    experiment = MagicMock()
    experiment_channel = MagicMock()
    experiment_channel.extra_data = {"page_id": PAGE_ID}
    service = MagicMock()
    service.voice_replies_supported = voice_replies_supported
    service.supported_message_types = ["text", "voice"]
    experiment_channel.messaging_provider.get_messaging_service.return_value = service
    channel = FacebookMessengerChannel(experiment=experiment, experiment_channel=experiment_channel)
    return channel, service


class TestFacebookMessengerChannel:
    def test_sender_and_callbacks_use_page_id_from_extra_data(self):
        channel, service = _make_channel()

        sender = channel._get_sender()
        callbacks = channel._get_callbacks()

        sender.send_text("hi", recipient=RECIPIENT)
        callbacks.echo_transcript(recipient=RECIPIENT, transcript="hi")
        for call in service.send_text_message.call_args_list:
            assert call.kwargs["from_"] == PAGE_ID

    @pytest.mark.parametrize("voice_replies_supported", [True, False])
    def test_capabilities_come_from_messaging_service(self, voice_replies_supported):
        channel, _ = _make_channel(voice_replies_supported=voice_replies_supported)

        capabilities = channel._get_capabilities()

        assert capabilities.supports_voice_replies is voice_replies_supported
        assert capabilities.supported_message_types == ("text", "voice")

    def test_file_sending_is_not_supported(self):
        """Facebook Messenger has never supported outbound files -- files fall back to download links."""
        channel, _ = _make_channel()

        capabilities = channel._get_capabilities()

        assert capabilities.supports_files is False
        assert capabilities.can_send_file(MagicMock()) is False

    def test_messaging_service_is_cached(self):
        channel, service = _make_channel()

        assert channel.messaging_service is service
        assert channel.messaging_service is service
        channel.experiment_channel.messaging_provider.get_messaging_service.assert_called_once()
