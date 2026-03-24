from unittest.mock import MagicMock, patch

import pytest

from apps.chat.channels import ChannelBase
from apps.chat.exceptions import ServiceWindowExpiredException


class TestSendMessageToUserVoiceFallback:
    """Tests for ChannelBase.send_message_to_user voice fallback behavior."""

    def _make_channel(self, voice_enabled=True):
        """Create a mock channel with the send_message_to_user method from ChannelBase."""
        channel = MagicMock(spec=ChannelBase)
        # Only bind the method under test; leave _reply_voice_message as a MagicMock
        # so .side_effect works correctly in tests
        channel.send_message_to_user = ChannelBase.send_message_to_user.__get__(channel)
        channel._format_reference_section = MagicMock(return_value=("test message", []))
        channel.append_attachment_links = MagicMock(side_effect=lambda msg, **kw: msg)
        channel._get_supported_unsupported_files = MagicMock(return_value=([], []))
        channel.supports_multimedia = False
        channel.message = None
        channel._bot_message_is_voice = False

        # Configure voice support
        channel.voice_replies_supported = voice_enabled
        if voice_enabled:
            channel.experiment = MagicMock()
            channel.experiment.synthetic_voice = MagicMock()
            channel.experiment.voice_response_behaviour = "always"

        return channel

    def test_voice_service_window_expired_falls_back_to_text(self):
        """When voice message raises ServiceWindowExpiredException, falls back to text."""
        channel = self._make_channel(voice_enabled=True)
        channel._reply_voice_message.side_effect = ServiceWindowExpiredException("window expired")
        channel._voice_fallback_to_text = ChannelBase._voice_fallback_to_text.__get__(channel)
        channel._send_text_to_user_with_notification = MagicMock()

        channel.send_message_to_user("Hello")

        channel._send_text_to_user_with_notification.assert_called_once()
        assert channel._bot_message_is_voice is False

    def test_text_service_window_expired_propagates(self):
        """When text message raises ServiceWindowExpiredException (no template), it propagates up."""
        channel = self._make_channel(voice_enabled=False)
        channel._send_text_to_user_with_notification = MagicMock(
            side_effect=ServiceWindowExpiredException("no template configured")
        )

        with pytest.raises(ServiceWindowExpiredException):
            channel.send_message_to_user("Hello")


class TestNotifyOnDeliveryFailureDecoratorPropagation:
    """Tests that @notify_on_delivery_failure does not swallow ServiceWindowExpiredException."""

    @staticmethod
    def _setup_channel_mock():
        """Create a mock channel with attributes needed by @notify_on_delivery_failure."""
        channel = MagicMock(spec=ChannelBase)
        # The decorator accesses these for the notification
        channel.experiment = MagicMock()
        channel._experiment_session = MagicMock()
        channel.experiment_channel = MagicMock()
        channel.experiment_channel.platform_enum.title.return_value = "WhatsApp"
        return channel

    def test_decorator_re_raises_service_window_expired(self):
        """The @notify_on_delivery_failure decorator logs and notifies but re-raises
        ServiceWindowExpiredException so it can be caught by send_message_to_user."""
        channel = self._setup_channel_mock()
        channel._send_voice_to_user_with_notification = ChannelBase._send_voice_to_user_with_notification.__get__(
            channel
        )
        channel.send_voice_to_user.side_effect = ServiceWindowExpiredException("window expired")

        with patch("apps.chat.decorators.message_delivery_failure_notification") as mock_notify:
            with pytest.raises(ServiceWindowExpiredException):
                channel._send_voice_to_user_with_notification(MagicMock())

            mock_notify.assert_called_once()

    def test_decorator_re_raises_service_window_expired_for_text(self):
        """The @notify_on_delivery_failure decorator re-raises ServiceWindowExpiredException
        from text message delivery as well."""
        channel = self._setup_channel_mock()
        channel._send_text_to_user_with_notification = ChannelBase._send_text_to_user_with_notification.__get__(channel)
        channel.send_text_to_user.side_effect = ServiceWindowExpiredException("no template")

        with patch("apps.chat.decorators.message_delivery_failure_notification") as mock_notify:
            with pytest.raises(ServiceWindowExpiredException):
                channel._send_text_to_user_with_notification("Hello")

            mock_notify.assert_called_once()
