from datetime import datetime
from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.whatsapp_channel import WhatsappCallbacks
from apps.channels.datamodels import MetaCloudAPIMessage
from apps.channels.models import ChannelPlatform


@pytest.fixture()
def service():
    return MagicMock()


@pytest.fixture()
def callbacks(service):
    return WhatsappCallbacks(service=service, from_number="15550001111")


def _make_ctx(last_activity_at=None, message=None):
    ctx = MagicMock()
    ctx.last_activity_at = last_activity_at
    ctx.message = message
    return ctx


class TestBindAndLastActivityAt:
    def test_ctx_is_none_before_bind(self, callbacks):
        assert callbacks._ctx is None

    def test_bind_stores_ctx(self, callbacks):
        ctx = _make_ctx()
        callbacks.bind(ctx)
        assert callbacks._ctx is ctx


class TestEchoTranscript:
    def test_forwards_last_activity_at(self, callbacks, service):
        ts = datetime(2024, 1, 15, 10, 30)
        callbacks.bind(_make_ctx(last_activity_at=ts))
        callbacks.echo_transcript("447700900000", "hello there")
        service.send_text_message.assert_called_once_with(
            message='I heard: "hello there"',
            from_="15550001111",
            to="447700900000",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=ts,
        )

    def test_passes_none_when_not_bound(self, callbacks, service):
        callbacks.echo_transcript("447700900000", "hello there")
        _, kwargs = service.send_text_message.call_args
        assert kwargs["last_activity_at"] is None


class TestSubmitInputToLlm:
    def test_sends_typing_indicator_for_meta_cloud_api_message(self, callbacks, service):
        message = MagicMock(spec=MetaCloudAPIMessage)
        message.whatsapp_message_id = "wamid.abc123"
        callbacks.bind(_make_ctx(message=message))

        callbacks.submit_input_to_llm("447700900000")

        service.send_typing_indicator.assert_called_once_with(
            from_="15550001111",
            message_id="wamid.abc123",
        )

    def test_skips_typing_indicator_without_message_id(self, callbacks, service):
        message = MagicMock(spec=MetaCloudAPIMessage)
        message.whatsapp_message_id = None
        callbacks.bind(_make_ctx(message=message))

        callbacks.submit_input_to_llm("447700900000")

        service.send_typing_indicator.assert_not_called()

    def test_skips_typing_indicator_for_non_meta_message(self, callbacks, service):
        callbacks.bind(_make_ctx(message=MagicMock()))
        callbacks.submit_input_to_llm("447700900000")
        service.send_typing_indicator.assert_not_called()

    def test_swallows_typing_indicator_errors(self, callbacks, service):
        message = MagicMock(spec=MetaCloudAPIMessage)
        message.whatsapp_message_id = "wamid.abc123"
        callbacks.bind(_make_ctx(message=message))
        service.send_typing_indicator.side_effect = Exception("network error")

        callbacks.submit_input_to_llm("447700900000")  # must not raise
