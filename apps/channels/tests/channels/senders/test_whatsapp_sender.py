from datetime import datetime
from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.whatsapp_channel import WhatsappSender
from apps.channels.models import ChannelPlatform


@pytest.fixture()
def service():
    return MagicMock()


@pytest.fixture()
def sender(service):
    return WhatsappSender(service=service, from_number="15550001111")


def _make_ctx(last_activity_at=None):
    ctx = MagicMock()
    ctx.last_activity_at = last_activity_at
    return ctx


class TestBindAndLastActivityAt:
    def test_last_activity_at_is_none_before_bind(self, sender):
        assert sender._last_activity_at is None

    def test_last_activity_at_reads_from_ctx_after_bind(self, sender):
        ts = datetime(2024, 1, 15, 10, 30)
        sender.bind(_make_ctx(last_activity_at=ts))
        assert sender._last_activity_at == ts

    def test_last_activity_at_none_when_ctx_has_no_session(self, sender):
        sender.bind(_make_ctx(last_activity_at=None))
        assert sender._last_activity_at is None


class TestSendText:
    def test_forwards_last_activity_at(self, sender, service):
        ts = datetime(2024, 1, 15, 10, 30)
        sender.bind(_make_ctx(last_activity_at=ts))
        sender.send_text("hello", "447700900000")
        service.send_text_message.assert_called_once_with(
            message="hello",
            from_="15550001111",
            to="447700900000",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=ts,
        )

    def test_passes_none_when_not_bound(self, sender, service):
        sender.send_text("hello", "447700900000")
        _, kwargs = service.send_text_message.call_args
        assert kwargs["last_activity_at"] is None


class TestSendVoice:
    def test_forwards_last_activity_at(self, sender, service):
        ts = datetime(2024, 1, 15, 10, 30)
        sender.bind(_make_ctx(last_activity_at=ts))
        audio = MagicMock()
        sender.send_voice(audio, "447700900000")
        _, kwargs = service.send_voice_message.call_args
        assert kwargs["last_activity_at"] == ts

    def test_passes_none_when_not_bound(self, sender, service):
        sender.send_voice(MagicMock(), "447700900000")
        _, kwargs = service.send_voice_message.call_args
        assert kwargs["last_activity_at"] is None


class TestSendFile:
    def test_forwards_last_activity_at(self, sender, service):
        ts = datetime(2024, 1, 15, 10, 30)
        sender.bind(_make_ctx(last_activity_at=ts))
        file = MagicMock()
        sender.send_file(file, "447700900000", session_id=99)
        _, kwargs = service.send_file_to_user.call_args
        assert kwargs["last_activity_at"] == ts

    def test_passes_none_when_not_bound(self, sender, service):
        file = MagicMock()
        sender.send_file(file, "447700900000", session_id=99)
        _, kwargs = service.send_file_to_user.call_args
        assert kwargs["last_activity_at"] is None

    def test_generates_download_link_with_session_id(self, sender, service):
        file = MagicMock()
        sender.send_file(file, "447700900000", session_id=42)
        file.download_link.assert_called_once_with(experiment_session_id=42)
        _, kwargs = service.send_file_to_user.call_args
        assert kwargs["download_link"] == file.download_link.return_value
