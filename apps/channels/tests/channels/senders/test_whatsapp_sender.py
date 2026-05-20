from datetime import datetime
from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.whatsapp_channel import WhatsappSender


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
