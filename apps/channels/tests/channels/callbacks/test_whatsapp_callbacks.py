from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.whatsapp_channel import WhatsappCallbacks
from apps.channels.datamodels import MetaCloudAPIMessage


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
