"""End-to-end behaviour of a channel an admin has disabled (issue #4200)."""

from unittest.mock import MagicMock, patch

import pytest

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.models import ChannelPlatform
from apps.channels.registry import PLATFORM_CHANNEL_CLASSES
from apps.channels.stages.core import AttachmentHydrationStage, ChannelDisabledStage
from apps.channels.tests.message_examples import base_messages
from apps.channels.web_channel import WebChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.tasks import get_response_for_webchat_task
from apps.utils.factories.channels import ExperimentChannelFactory
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

    def test_inbound_message_is_never_recorded(self):
        """The user's message is dropped -- ChatMessageCreationStage never runs."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message())

        assert not ChatMessage.objects.filter(chat=session.chat, message_type=ChatMessageType.HUMAN).exists()

    def test_static_reply_is_recorded_when_the_channel_carries_a_session(self):
        """Web and Slack pre-set the session, so the reply lands in history like any other
        early exit on those channels. Channels that resolve a session lazily never get that
        far, so nothing is written for them."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message())

        messages = ChatMessage.objects.filter(chat=session.chat)
        assert [(m.message_type, m.content) for m in messages] == [(ChatMessageType.AI, "We are closed")]

    def test_silent_disable_writes_nothing(self):
        """EarlyAbort skips the terminal stages, so not even the session is touched."""
        session = ExperimentSessionFactory.create()
        _disable(session)
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message())

        assert ChatMessage.objects.filter(chat=session.chat).count() == 0

    def test_no_session_is_created_for_a_new_participant(self):
        """A disabled channel must not spin up conversations for people it is turned off for."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")
        channel = StubChannel(session.experiment, session.experiment_channel)
        channel.trace_service = make_trace_service()

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message(participant_id="brand-new"))

        assert channel.text_sent == ["We are closed"]
        assert not session.experiment.sessions.filter(participant__identifier="brand-new").exists()


@pytest.mark.django_db()
class TestDisabledEmbeddedWidget:
    """Widget traffic is served by WebChannel, not ApiChannel, so the toggle has to be
    enforced on that pipeline too -- otherwise the badge and the dialog warning show while
    the bot keeps answering every widget message."""

    def _widget_session(self, disabled_message=""):
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": "tok"},
            enabled=False,
            disabled_message=disabled_message,
        )
        return ExperimentSessionFactory.create(
            experiment=channel.experiment, team=channel.team, experiment_channel=channel
        )

    def test_widget_message_gets_the_static_reply(self):
        session = self._widget_session("The bot is offline for maintenance")

        with patch("apps.chat.bots.PipelineBot.process_input") as process_input:
            result = get_response_for_webchat_task(
                experiment_session_id=session.id, experiment_id=session.experiment.id, message_text="hello"
            )

        assert result["response"] == "The bot is offline for maintenance"
        process_input.assert_not_called()

    def test_widget_message_is_ignored_when_no_static_reply_is_configured(self):
        session = self._widget_session()

        with patch("apps.chat.bots.PipelineBot.process_input") as process_input:
            result = get_response_for_webchat_task(
                experiment_session_id=session.id, experiment_id=session.experiment.id, message_text="hello"
            )

        assert result["response"] == ""
        process_input.assert_not_called()


@pytest.mark.django_db()
class TestDisabledStageIsWired:
    """Every channel a participant can reach must honour the toggle."""

    def _core_stages(self, channel_cls):
        stub_self = MagicMock(attachment_hydration_stage_class=AttachmentHydrationStage)
        return channel_cls._build_pipeline(stub_self).core_stages

    @pytest.mark.parametrize("platform", sorted(PLATFORM_CHANNEL_CLASSES, key=str))
    def test_every_registered_platform_enforces_the_toggle(self, platform):
        stages = self._core_stages(PLATFORM_CHANNEL_CLASSES[platform])

        assert any(isinstance(stage, ChannelDisabledStage) for stage in stages), (
            f"{platform} does not enforce the channel enabled/disabled toggle"
        )

    @pytest.mark.parametrize("channel_cls", [ChannelBase, ApiChannel, WebChannel])
    def test_pipelines_that_are_not_reached_through_the_registry(self, channel_cls):
        stages = self._core_stages(channel_cls)

        assert any(isinstance(stage, ChannelDisabledStage) for stage in stages)

    def test_evaluations_are_deliberately_exempt(self):
        """An eval run is not participant access; substituting the static message for the
        bot's output would corrupt the run."""
        stages = self._core_stages(EvaluationChannel)

        assert not any(isinstance(stage, ChannelDisabledStage) for stage in stages)
