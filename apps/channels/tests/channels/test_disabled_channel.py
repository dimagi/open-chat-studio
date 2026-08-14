"""End-to-end behaviour of a channel an admin has disabled (issue #4200)."""

from unittest.mock import patch

import pytest

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.stages.core import ChannelDisabledStage
from apps.channels.tests.message_examples import base_messages
from apps.chat.models import ChatMessage
from apps.utils.factories.experiment import ExperimentSessionFactory

from .conftest import StubChannel, make_trace_service


def _disable(session, message=""):
    channel = session.experiment_channel
    channel.enabled = False
    channel.disabled_message = message
    channel.save()
    return channel


def _make_channel(session):
    channel = StubChannel(session.experiment, session.experiment_channel, session)
    channel.trace_service = make_trace_service()
    return channel


@pytest.mark.django_db()
class TestDisabledChannel:
    def test_static_message_is_returned_and_sent(self):
        session = ExperimentSessionFactory.create()
        _disable(session, "The bot is offline for maintenance")
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot") as get_bot:
            response = channel.new_user_message(base_messages.text_message(participant_id="123"))

        assert response.content == "The bot is offline for maintenance"
        assert channel.text_sent == ["The bot is offline for maintenance"]
        assert channel._sender.text_messages[0][1] == "123"
        get_bot.assert_not_called()

    def test_no_message_means_no_reply(self):
        session = ExperimentSessionFactory.create()
        _disable(session)
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot") as get_bot:
            response = channel.new_user_message(base_messages.text_message())

        assert response.content == ""
        assert channel.text_sent == []
        get_bot.assert_not_called()

    @pytest.mark.parametrize(
        "disabled_message",
        [
            pytest.param("We are closed", id="with_static_message"),
            pytest.param("", id="silent"),
        ],
    )
    def test_nothing_is_persisted(self, disabled_message):
        """Neither the inbound message nor the static reply is written to chat history."""
        session = ExperimentSessionFactory.create()
        _disable(session, disabled_message)
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message())

        assert ChatMessage.objects.filter(chat=session.chat).count() == 0


@pytest.mark.django_db()
class TestDisabledStageIsWired:
    """The stage must be first so a disabled channel does no work at all."""

    def test_base_pipeline(self):
        session = ExperimentSessionFactory.create()
        pipeline = _make_channel(session)._build_pipeline()
        assert isinstance(pipeline.core_stages[0], ChannelDisabledStage)

    def test_api_pipeline(self):
        """ApiChannel also backs the embedded widget, which is admin-configurable."""
        session = ExperimentSessionFactory.create()
        channel = ApiChannel(session.experiment, session.experiment_channel, session)
        assert isinstance(channel._build_pipeline().core_stages[0], ChannelDisabledStage)

    def test_enabled_channel_runs_the_full_pipeline(self):
        session = ExperimentSessionFactory.create()
        assert session.experiment_channel.enabled is True
        channel = _make_channel(session)
        stage = channel._build_pipeline().core_stages[0]
        ctx = ChannelBase._create_context(channel, base_messages.text_message())

        assert stage.should_run(ctx) is False
