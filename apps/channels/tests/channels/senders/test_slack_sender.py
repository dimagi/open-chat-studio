from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.slack_channel import SlackSender
from apps.channels.datamodels import SlackMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tests.channels.conftest import make_context
from apps.slack.utils import make_session_external_id


@pytest.fixture()
def slack_service():
    return MagicMock()


@pytest.fixture()
def sender(slack_service):
    return SlackSender(service=slack_service)


def _bind_with_message(sender):
    message = SlackMessage(
        participant_id="SLACK_USER_ID",
        channel_id="channel_from_message",
        thread_ts="thread_from_message",
        message_text="Hello",
    )
    ctx = make_context(message=message)
    sender.bind(ctx)
    return ctx


def _bind_without_message(sender):
    session = MagicMock()
    session.external_id = make_session_external_id("channel_from_session", "thread_from_session")
    ctx = make_context(experiment_session=session)
    ctx.message = None  # Ad hoc sends have no inbound message
    sender.bind(ctx)
    return ctx


class TestSendText:
    def test_uses_channel_and_thread_from_inbound_message(self, sender, slack_service):
        _bind_with_message(sender)
        sender.send_text("hello", "SLACK_USER_ID")
        slack_service.send_text_message.assert_called_once_with(
            "hello",
            from_="",
            to="channel_from_message",
            platform=ChannelPlatform.SLACK,
            thread_ts="thread_from_message",
        )

    def test_falls_back_to_session_external_id(self, sender, slack_service):
        _bind_without_message(sender)
        sender.send_text("hello", "SLACK_USER_ID")
        slack_service.send_text_message.assert_called_once_with(
            "hello",
            from_="",
            to="channel_from_session",
            platform=ChannelPlatform.SLACK,
            thread_ts="thread_from_session",
        )


class TestSendVoice:
    def test_not_supported(self, sender):
        with pytest.raises(NotImplementedError):
            sender.send_voice(MagicMock(), "SLACK_USER_ID")


class TestSendFile:
    def test_uses_channel_and_thread_from_inbound_message(self, sender, slack_service):
        _bind_with_message(sender)
        file = MagicMock()
        sender.send_file(file, "SLACK_USER_ID", session_id=42)
        slack_service.send_file_message.assert_called_once_with(
            file=file,
            to="channel_from_message",
            thread_ts="thread_from_message",
        )

    def test_falls_back_to_session_external_id(self, sender, slack_service):
        _bind_without_message(sender)
        file = MagicMock()
        sender.send_file(file, "SLACK_USER_ID", session_id=42)
        slack_service.send_file_message.assert_called_once_with(
            file=file,
            to="channel_from_session",
            thread_ts="thread_from_session",
        )
