from unittest.mock import patch

import pytest
from waffle.testutils import override_flag

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.stages.core import AbuseRulesStage, BotInteractionStage, ChatMessageCreationStage
from apps.channels.tests.channels.conftest import core_stages, make_context, messaging_channel
from apps.channels.tests.message_examples import base_messages
from apps.channels.web_channel import WebChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.chat.schemas import ModerationDetail
from apps.moderation.models import DeniedParticipant
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory

FLAG = Flags.ABUSE_DETECTION.slug


def _context(session, text):
    human_message = ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content=text)
    return make_context(
        experiment=session.experiment,
        experiment_session=session,
        user_query=text,
        human_message=human_message,
    )


@pytest.mark.django_db()
class TestAbuseRulesStage:
    @override_flag(FLAG, active=False)
    def test_does_not_run_when_flag_off(self):
        session = ExperimentSessionFactory.create()
        ctx = _context(session, "<script>alert(1)</script>")

        assert AbuseRulesStage().should_run(ctx) is False

    @override_flag(FLAG, active=True)
    def test_does_not_run_without_human_message(self):
        session = ExperimentSessionFactory.create()
        ctx = make_context(experiment=session.experiment, experiment_session=session, user_query="<script>")

        assert AbuseRulesStage().should_run(ctx) is False

    @override_flag(FLAG, active=True)
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("<script>alert(1)</script>", ModerationDetail(rules=["markup"]), id="rule_fires"),
            pytest.param("Is 3 < 5?", None, id="no_rule_fires"),
        ],
    )
    def test_records_rule_hits(self, text, expected):
        session = ExperimentSessionFactory.create()
        ctx = _context(session, text)

        AbuseRulesStage()(ctx)

        ctx.human_message.refresh_from_db()
        assert ctx.human_message.moderation_detail == expected


@pytest.mark.django_db()
@override_flag(FLAG, active=True)
def test_markup_message_is_recorded_and_answered():
    session = ExperimentSessionFactory.create()
    channel = messaging_channel(session)

    with patch("apps.channels.stages.core.get_bot") as get_bot:
        get_bot.return_value.process_input.return_value = ChatMessage(content="Hello", message_type=ChatMessageType.AI)
        channel.new_user_message(
            base_messages.text_message(
                participant_id=session.participant.identifier, message_text="<script>alert(1)</script>"
            )
        )

    human_message = ChatMessage.objects.get(chat=session.chat, message_type=ChatMessageType.HUMAN)
    assert human_message.moderation_detail == ModerationDetail(rules=["markup"])
    assert channel.text_sent == ["Hello"]
    assert not DeniedParticipant.objects.filter(participant=session.participant).exists()


class TestAbuseRulesStageIsWired:
    @pytest.mark.parametrize(
        "channel_cls",
        [
            pytest.param(ChannelBase, id="messaging"),
            pytest.param(ApiChannel, id="api"),
            pytest.param(WebChannel, id="web"),
        ],
    )
    def test_runs_after_message_creation_and_before_bot(self, channel_cls):
        types = [type(stage) for stage in core_stages(channel_cls)]

        assert types.count(AbuseRulesStage) == 1
        assert types.index(ChatMessageCreationStage) < types.index(AbuseRulesStage) < types.index(BotInteractionStage)

    def test_evaluation_channel_is_unaffected(self):
        assert AbuseRulesStage not in [type(stage) for stage in core_stages(EvaluationChannel)]
