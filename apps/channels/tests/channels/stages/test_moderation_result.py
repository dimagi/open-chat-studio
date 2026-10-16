from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import TimeoutError as CeleryTimeoutError
from waffle.testutils import override_flag

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.exceptions import EarlyExitResponse
from apps.channels.stages.terminal import ModerationResultStage
from apps.channels.tests.channels.conftest import make_context, messaging_channel
from apps.channels.tests.message_examples import base_messages
from apps.channels.web_channel import WebChannel
from apps.chat.models import ChatMessage, ChatMessageMetadataKeys, ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.moderation.const import FLAGGED_MESSAGE, MODERATION_WAIT_TIMEOUT
from apps.service_providers.moderation_service.schemas import ModerationOutput
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ChatMessageFactory

FLAG = Flags.ABUSE_DETECTION.slug
EVENT_BOT = "apps.channels.stages.terminal.EventBot"
NOTIFICATION = "apps.channels.stages.terminal.moderation_skipped_notification"

PASSED_OUTPUT = {"illicit": {"flagged": False, "score": 0.01}, "hate": {"flagged": False, "score": 0.02}}
NON_STRIKE_OUTPUT = {"illicit": {"flagged": False, "score": 0.01}, "harassment": {"flagged": True, "score": 0.7}}
STRIKE_OUTPUT = {"illicit/violent": {"flagged": True, "score": 0.84}, "hate": {"flagged": False, "score": 0.02}}


def _ok(output: dict) -> dict:
    return {"status": "ok", "output": output}


def _terminal_stage_types(channel_cls):
    stub_self = MagicMock()
    return [type(stage) for stage in channel_cls._build_pipeline(stub_self).terminal_stages]


def _context(session, *, bot_response=None, **kwargs):
    human_message = ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="text")
    return make_context(
        experiment=session.experiment,
        experiment_session=session,
        user_query="text",
        human_message=human_message,
        bot_response=bot_response,
        moderation_task_id="task-1",
        **kwargs,
    )


def _bot_response(session, content="The bot's answer"):
    return ChatMessage.objects.create(
        chat=session.chat,
        message_type=ChatMessageType.AI,
        content=content,
        metadata={ChatMessageMetadataKeys.OCS_ATTACHMENT_FILE_IDS: [1], "trace_info": {"trace_id": "t-1"}},
    )


class TestShouldRun:
    def test_does_not_run_without_a_task(self):
        ctx = make_context(human_message=MagicMock(), moderation_task_id=None)

        assert ModerationResultStage().should_run(ctx) is False

    def test_runs_with_a_task(self):
        ctx = make_context(human_message=MagicMock(), moderation_task_id="task-1")

        assert ModerationResultStage().should_run(ctx) is True


@pytest.mark.django_db()
class TestModerationResultStage:
    def test_waits_for_the_task(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(PASSED_OUTPUT)

        ModerationResultStage()(_context(moderated_session))

        moderation_task.async_result.assert_called_once_with("task-1")
        moderation_task.get.assert_called_once_with(timeout=MODERATION_WAIT_TIMEOUT, disable_sync_subtasks=False)

    @pytest.mark.parametrize(
        ("output", "expected_status"),
        [
            pytest.param(PASSED_OUTPUT, ModerationStatus.PASSED, id="passed"),
            pytest.param(NON_STRIKE_OUTPUT, ModerationStatus.PASSED, id="only_non_strike_categories_flagged"),
            pytest.param(STRIKE_OUTPUT, ModerationStatus.FLAGGED, id="strike_category_flagged"),
        ],
    )
    def test_records_the_output(self, moderated_session, moderation_task, output, expected_status):
        moderation_task.get.return_value = _ok(output)
        ctx = _context(moderated_session)

        with patch(EVENT_BOT), patch(NOTIFICATION) as notification:
            ModerationResultStage()(ctx)

        ctx.human_message.refresh_from_db()
        assert ctx.human_message.moderation_status == expected_status
        assert ctx.human_message.moderation_detail == ModerationDetail(output=ModerationOutput.model_validate(output))
        notification.assert_not_called()

    @pytest.mark.parametrize(
        ("wait", "expected_reason"),
        [
            pytest.param(
                {"return_value": {"status": "skipped", "reason": "rate_limited"}}, "rate_limited", id="rate_limited"
            ),
            pytest.param({"return_value": {"status": "skipped", "reason": "error"}}, "error", id="task_error"),
            pytest.param({"side_effect": CeleryTimeoutError()}, "timeout", id="timeout"),
            pytest.param({"side_effect": RuntimeError("boom")}, "error", id="task_raised"),
            pytest.param({"return_value": _ok({"hate": {"flagged": True, "score": 2}})}, "error", id="invalid_output"),
        ],
    )
    def test_records_a_skip_and_notifies(self, moderated_session, moderation_task, wait, expected_reason):
        moderation_task.get.configure_mock(**wait)
        ctx = _context(moderated_session, bot_response=_bot_response(moderated_session), formatted_message="answer")

        with patch(NOTIFICATION) as notification:
            ModerationResultStage()(ctx)

        ctx.human_message.refresh_from_db()
        assert ctx.human_message.moderation_status == ModerationStatus.SKIPPED
        assert ctx.human_message.moderation_detail == ModerationDetail(skip_reason=expected_reason)
        notification.assert_called_once_with(
            moderated_session.experiment, session=moderated_session, reason=expected_reason
        )
        assert ctx.formatted_message == "answer"
        assert ctx.early_exit_response is None

    def test_keeps_rule_hits(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(PASSED_OUTPUT)
        ctx = _context(moderated_session)
        ctx.human_message.moderation_detail = ModerationDetail(rules=["markup"])
        ctx.human_message.save()

        ModerationResultStage()(ctx)

        ctx.human_message.refresh_from_db()
        assert ctx.human_message.moderation_detail.rules == ["markup"]
        assert ctx.human_message.moderation_detail.output is not None

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param(PASSED_OUTPUT, id="passed"),
            pytest.param(NON_STRIKE_OUTPUT, id="only_non_strike_categories_flagged"),
        ],
    )
    def test_passed_message_keeps_the_response(self, moderated_session, moderation_task, output):
        moderation_task.get.return_value = _ok(output)
        bot_response = _bot_response(moderated_session)
        ctx = _context(moderated_session, bot_response=bot_response, formatted_message="answer")

        with patch(EVENT_BOT) as event_bot:
            ModerationResultStage()(ctx)

        event_bot.assert_not_called()
        bot_response.refresh_from_db()
        assert bot_response.content == "The bot's answer"
        assert ctx.formatted_message == "answer"
        assert ctx.early_exit_response is None

    def test_flagged_message_replaces_the_response(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)
        bot_response = _bot_response(moderated_session)
        ctx = _context(
            moderated_session,
            bot_response=bot_response,
            formatted_message="answer",
            voice_audio=MagicMock(),
            additional_text_message="https://example.com",
            files_to_send=[MagicMock()],
            unsupported_files=[MagicMock()],
        )

        with patch(EVENT_BOT) as event_bot:
            event_bot.return_value.get_user_message.return_value = "Votre message a été signalé."
            ModerationResultStage()(ctx)

        bot_response.refresh_from_db()
        assert bot_response.content == "Votre message a été signalé."
        assert bot_response.metadata == {"trace_info": {"trace_id": "t-1"}}
        assert ctx.early_exit_response == "Votre message a été signalé."
        assert ctx.formatted_message is None
        assert ctx.voice_audio is None
        assert ctx.additional_text_message is None
        assert ctx.files_to_send == []
        assert ctx.unsupported_files == []

    @pytest.mark.parametrize(
        "event_bot_reply",
        [
            pytest.param({"side_effect": Exception("no LLM provider")}, id="event_bot_fails"),
            pytest.param({"return_value": ""}, id="event_bot_returns_nothing"),
        ],
    )
    def test_flagged_message_falls_back_to_a_fixed_reply(self, moderated_session, moderation_task, event_bot_reply):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)
        ctx = _context(moderated_session, bot_response=_bot_response(moderated_session))

        with patch(EVENT_BOT) as event_bot:
            event_bot.return_value.get_user_message.configure_mock(**event_bot_reply)
            ModerationResultStage()(ctx)

        assert ctx.early_exit_response == FLAGGED_MESSAGE
        assert ctx.bot_response.content == FLAGGED_MESSAGE

    def test_flagged_message_without_a_bot_response_keeps_the_early_exit(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)
        ctx = _context(moderated_session, early_exit_response="Something went wrong")

        with patch(EVENT_BOT) as event_bot:
            ModerationResultStage()(ctx)

        event_bot.assert_not_called()
        ctx.human_message.refresh_from_db()
        assert ctx.human_message.moderation_status == ModerationStatus.FLAGGED
        assert ctx.early_exit_response == "Something went wrong"


@pytest.mark.django_db()
class TestModerationInTheChannelPipeline:
    @pytest.fixture(autouse=True)
    def _flag_on(self):
        with override_flag(FLAG, active=True):
            yield

    def _send(self, session, *, process_input):
        channel = messaging_channel(session)
        with patch("apps.channels.stages.core.get_bot") as get_bot:
            get_bot.return_value.process_input.side_effect = process_input
            channel.new_user_message(
                base_messages.text_message(participant_id=session.participant.identifier, message_text="Bad words")
            )
        return channel

    def _human_message(self, session):
        return ChatMessage.objects.get(chat=session.chat, message_type=ChatMessageType.HUMAN)

    def test_flagged_message_gets_the_replacement_reply(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)

        with patch(EVENT_BOT) as event_bot:
            event_bot.return_value.get_user_message.return_value = "Your message was flagged."
            channel = self._send(
                moderated_session, process_input=lambda *args, **kwargs: _bot_response(moderated_session)
            )

        moderation_task.task.delay.assert_called_once_with(
            text="Bad words", moderation_provider_id=moderated_session.experiment.team.moderation_provider_id
        )
        assert channel.text_sent == ["Your message was flagged."]
        assert self._human_message(moderated_session).moderation_status == ModerationStatus.FLAGGED
        ai_messages = ChatMessage.objects.filter(chat=moderated_session.chat, message_type=ChatMessageType.AI)
        assert [message.content for message in ai_messages] == ["Your message was flagged."]

    def test_passed_message_is_answered(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(PASSED_OUTPUT)

        channel = self._send(moderated_session, process_input=lambda *args, **kwargs: _bot_response(moderated_session))

        assert channel.text_sent == ["The bot's answer"]
        assert self._human_message(moderated_session).moderation_status == ModerationStatus.PASSED

    def test_runs_after_an_early_exit_in_a_core_stage(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)

        channel = self._send(moderated_session, process_input=EarlyExitResponse("Stopped early"))

        assert channel.text_sent == ["Stopped early"]
        assert self._human_message(moderated_session).moderation_status == ModerationStatus.FLAGGED

    @override_flag(FLAG, active=False)
    def test_flag_off_starts_no_task(self, moderated_session, moderation_task):
        channel = self._send(moderated_session, process_input=lambda *args, **kwargs: _bot_response(moderated_session))

        moderation_task.task.delay.assert_not_called()
        moderation_task.async_result.assert_not_called()
        assert channel.text_sent == ["The bot's answer"]
        assert self._human_message(moderated_session).moderation_status is None


class TestModerationResultStageIsWired:
    @pytest.mark.parametrize(
        "channel_cls",
        [
            pytest.param(ChannelBase, id="messaging"),
            pytest.param(ApiChannel, id="api"),
            pytest.param(WebChannel, id="web"),
        ],
    )
    def test_is_the_first_terminal_stage(self, channel_cls):
        assert _terminal_stage_types(channel_cls)[0] is ModerationResultStage

    def test_evaluation_channel_is_unaffected(self):
        assert ModerationResultStage not in _terminal_stage_types(EvaluationChannel)
