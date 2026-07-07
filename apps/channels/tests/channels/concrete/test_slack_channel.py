from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.slack_channel import SlackChannel
from apps.channels.const import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException


def _make_channel(messaging_service=None):
    return SlackChannel(
        experiment=MagicMock(),
        experiment_channel=MagicMock(),
        experiment_session=MagicMock(),
        messaging_service=messaging_service,
    )


class TestSlackChannelInit:
    def test_requires_existing_session(self):
        with pytest.raises(ChannelException, match="SlackChannel requires an existing session"):
            SlackChannel(
                experiment=MagicMock(),
                experiment_channel=MagicMock(),
            )

    def test_accepts_session(self):
        channel = _make_channel()
        assert channel.experiment_session is not None


class TestSlackChannelMessagingService:
    def test_injected_service_is_used(self):
        service = MagicMock()
        channel = _make_channel(messaging_service=service)
        assert channel.messaging_service is service
        channel.experiment_channel.messaging_provider.get_messaging_service.assert_not_called()

    def test_lazy_service_from_provider(self):
        channel = _make_channel()
        service = channel.messaging_service
        assert service is channel.experiment_channel.messaging_provider.get_messaging_service.return_value
        # Cached on subsequent access
        assert channel.messaging_service is service
        channel.experiment_channel.messaging_provider.get_messaging_service.assert_called_once()


class TestSlackChannelCapabilities:
    def test_capabilities(self):
        channel = _make_channel()
        caps = channel._get_capabilities()
        assert caps.supports_voice_replies is False
        assert caps.supports_files is True
        assert caps.supports_conversational_consent is True
        assert caps.supported_message_types == (MESSAGE_TYPES.TEXT,)

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected"),
        [
            pytest.param("application/pdf", 2 * 1024 * 1024, True, id="pdf_within_limit"),
            pytest.param("image/png", 60 * 1024 * 1024, False, id="image_over_50mb"),
            pytest.param("text/csv", 1024, False, id="unsupported_type"),
        ],
    )
    def test_can_send_file(self, content_type, content_size, expected):
        channel = _make_channel()
        file = MagicMock()
        file.content_type = content_type
        file.content_size = content_size
        assert channel._can_send_file(file) is expected
