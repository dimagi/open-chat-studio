from unittest.mock import MagicMock, patch

import pytest
from waffle.testutils import override_flag

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.exceptions import EarlyAbort
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.stages.core import AttachmentHydrationStage, BlockedParticipantStage, ParticipantResolverStage
from apps.channels.tests.channels.conftest import StubChannel, make_context, make_trace_service
from apps.channels.tests.message_examples import base_messages
from apps.channels.web_channel import WebChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.tasks import get_response_for_webchat_task
from apps.teams.flags import Flags
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.moderation import DeniedParticipantFactory

FLAG = Flags.ABUSE_DETECTION.slug


def _block(session):
    DeniedParticipantFactory.create(team=session.team, participant=session.participant)


def _messaging_channel(session):
    """A stub messaging channel whose inbound messages resolve to the session's participant."""
    session.participant.platform = session.experiment_channel.platform
    session.participant.save()
    channel = StubChannel(session.experiment, session.experiment_channel, session)
    channel.trace_service = make_trace_service()
    return channel


@pytest.mark.django_db()
class TestBlockedParticipantStage:
    @override_flag(FLAG, active=False)
    def test_does_not_run_when_flag_off(self):
        session = ExperimentSessionFactory.create()
        ctx = make_context(experiment=session.experiment, participant=session.participant)

        assert BlockedParticipantStage().should_run(ctx) is False

    @override_flag(FLAG, active=True)
    def test_allowed_participant_is_a_no_op(self):
        session = ExperimentSessionFactory.create()
        ctx = make_context(experiment=session.experiment, participant=session.participant)

        BlockedParticipantStage()(ctx)

    @override_flag(FLAG, active=True)
    def test_blocked_participant_aborts(self):
        session = ExperimentSessionFactory.create()
        _block(session)
        ctx = make_context(experiment=session.experiment, participant=session.participant)

        with pytest.raises(EarlyAbort):
            BlockedParticipantStage()(ctx)

    @override_flag(FLAG, active=True)
    def test_falls_back_to_the_session_participant(self):
        """Web channels pre-set the session and never resolve ctx.participant."""
        session = ExperimentSessionFactory.create()
        _block(session)
        ctx = make_context(experiment=session.experiment, experiment_session=session)

        with pytest.raises(EarlyAbort):
            BlockedParticipantStage()(ctx)


@pytest.mark.django_db()
class TestBlockedParticipantPerChannel:
    @override_flag(FLAG, active=True)
    def test_messaging_channel_sends_no_reply(self):
        session = ExperimentSessionFactory.create()
        _block(session)
        channel = _messaging_channel(session)

        with patch("apps.channels.stages.core.get_bot") as get_bot:
            response = channel.new_user_message(
                base_messages.text_message(participant_id=session.participant.identifier)
            )

        assert response.content == ""
        assert channel.text_sent == []
        get_bot.assert_not_called()
        assert not ChatMessage.objects.filter(chat=session.chat).exists()

    @override_flag(FLAG, active=False)
    def test_messaging_channel_replies_when_flag_off(self):
        session = ExperimentSessionFactory.create()
        _block(session)
        channel = _messaging_channel(session)

        with patch("apps.channels.stages.core.get_bot") as get_bot:
            get_bot.return_value.process_input.return_value = ChatMessage(
                content="Hello", message_type=ChatMessageType.AI
            )
            channel.new_user_message(base_messages.text_message(participant_id=session.participant.identifier))

        assert channel.text_sent == ["Hello"]

    @override_flag(FLAG, active=True)
    def test_web_widget_gets_no_reply(self):
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET, extra_data={"widget_token": "tok"}
        )
        session = ExperimentSessionFactory.create(
            experiment=channel.experiment, team=channel.team, experiment_channel=channel
        )
        _block(session)
        last_activity_at = session.last_activity_at

        with patch("apps.chat.bots.PipelineBot.process_input") as process_input:
            result = get_response_for_webchat_task(
                experiment_session_id=session.id, experiment_id=session.experiment.id, message_text="hello"
            )

        assert result["response"] == ""
        process_input.assert_not_called()
        assert not ChatMessage.objects.filter(chat=session.chat).exists()
        session.refresh_from_db()
        assert session.last_activity_at == last_activity_at

    @override_flag(FLAG, active=True)
    def test_api_channel_gets_no_reply(self):
        session = ExperimentSessionFactory.create()
        session.experiment_channel = ExperimentChannel.objects.get_team_api_channel(session.team)
        session.save()
        session.participant.platform = ChannelPlatform.API
        session.participant.save()
        _block(session)
        last_activity_at = session.last_activity_at
        channel = ApiChannel(session.experiment, session.experiment_channel, session)

        with patch("apps.channels.stages.core.get_bot") as get_bot:
            response = channel.new_user_message(
                base_messages.text_message(participant_id=session.participant.identifier)
            )

        assert response.content == ""
        get_bot.assert_not_called()
        assert not ChatMessage.objects.filter(chat=session.chat).exists()
        session.refresh_from_db()
        assert session.last_activity_at == last_activity_at


class TestBlockedParticipantStageIsWired:
    def _core_stages(self, channel_cls):
        stub_self = MagicMock(attachment_hydration_stage_class=AttachmentHydrationStage)
        return channel_cls._build_pipeline(stub_self).core_stages

    @pytest.mark.parametrize(
        "channel_cls",
        [
            pytest.param(ChannelBase, id="messaging"),
            pytest.param(ApiChannel, id="api"),
            pytest.param(WebChannel, id="web"),
        ],
    )
    def test_stage_is_in_pipeline(self, channel_cls):
        stages = [stage for stage in self._core_stages(channel_cls) if isinstance(stage, BlockedParticipantStage)]

        assert len(stages) == 1

    @pytest.mark.parametrize(
        "channel_cls", [pytest.param(ChannelBase, id="messaging"), pytest.param(ApiChannel, id="api")]
    )
    def test_runs_directly_after_participant_resolution(self, channel_cls):
        types = [type(stage) for stage in self._core_stages(channel_cls)]

        assert types.index(BlockedParticipantStage) == types.index(ParticipantResolverStage) + 1

    def test_evaluation_channel_is_unaffected(self):
        """Evaluation runs share one internal participant, which must never be blocked."""
        stages = self._core_stages(EvaluationChannel)

        assert not any(isinstance(stage, BlockedParticipantStage) for stage in stages)
