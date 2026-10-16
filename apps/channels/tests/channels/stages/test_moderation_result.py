from unittest.mock import MagicMock, patch

import openai
import pytest
from django.db import DatabaseError
from waffle.testutils import override_flag

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.exceptions import EarlyExitResponse
from apps.channels.stages.terminal import ModerationResultStage
from apps.channels.tests.channels.conftest import make_context, make_trace_service, messaging_channel
from apps.channels.tests.message_examples import base_messages
from apps.channels.web_channel import WebChannel
from apps.chat.models import ChatMessage, ChatMessageMetadataKeys, ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.experiments.models import SessionStatus
from apps.moderation.const import FLAGGED_MESSAGE
from apps.service_providers.moderation_service.schemas import ModerationOutput
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ChatMessageFactory
from apps.utils.factories.pipelines import PipelineChatHistoryFactory, PipelineChatMessagesFactory
from apps.utils.tests.provider_errors import connection_error

FLAG = Flags.ABUSE_DETECTION.slug
EVENT_BOT = "apps.channels.pipeline.EventBot"
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


@pytest.mark.parametrize(
    ("human_message", "task_id", "expected"),
    [
        pytest.param(MagicMock(), "task-1", True, id="task_and_message"),
        pytest.param(MagicMock(), None, False, id="no_task"),
        pytest.param(None, "task-1", False, id="no_human_message"),
    ],
)
def test_should_run(human_message, task_id, expected):
    ctx = make_context(human_message=human_message, moderation_task_id=task_id)

    assert ModerationResultStage().should_run(ctx) is expected


@pytest.mark.django_db()
class TestModerationResultStage:
    @pytest.mark.parametrize(
        ("output", "expected_status", "expected_reply"),
        [
            pytest.param(PASSED_OUTPUT, ModerationStatus.PASSED, "answer", id="passed"),
            pytest.param(NON_STRIKE_OUTPUT, ModerationStatus.PASSED, "answer", id="only_non_strike_categories_flagged"),
            pytest.param(STRIKE_OUTPUT, ModerationStatus.FLAGGED, "Flagged reply", id="strike_category_flagged"),
        ],
    )
    def test_records_the_output(self, moderated_session, moderation_task, output, expected_status, expected_reply):
        moderation_task.get.return_value = _ok(output)
        bot_response = _bot_response(moderated_session)
        ctx = _context(moderated_session, bot_response=bot_response, formatted_message="answer")

        with patch(EVENT_BOT) as event_bot, patch(NOTIFICATION) as notification:
            event_bot.return_value.get_user_message.return_value = "Flagged reply"
            ModerationResultStage()(ctx)

        ctx.human_message.refresh_from_db()
        assert ctx.human_message.moderation_status == expected_status
        assert ctx.human_message.moderation_detail == ModerationDetail(output=ModerationOutput.model_validate(output))
        notification.assert_not_called()
        bot_response.refresh_from_db()
        assert bot_response.content == ("The bot's answer" if expected_reply == "answer" else expected_reply)
        assert ctx.formatted_message == expected_reply
        assert ctx.early_exit_response is None

    @pytest.mark.parametrize(
        ("wait", "expected_reason"),
        [
            pytest.param(
                {"return_value": {"status": "skipped", "reason": "rate_limited"}}, "rate_limited", id="rate_limited"
            ),
            pytest.param({"return_value": {"status": "skipped", "reason": "error"}}, "error", id="task_error"),
            pytest.param(
                {"side_effect": connection_error(openai.APIConnectionError)}, "error", id="task_failed_after_retries"
            ),
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
        assert ctx.formatted_message == "Votre message a été signalé."
        assert ctx.early_exit_response is None
        assert ctx.voice_audio is None
        assert ctx.additional_text_message is None
        assert ctx.files_to_send == []
        assert ctx.unsupported_files == []

    @pytest.mark.parametrize(
        ("output", "expected_rows"),
        [
            pytest.param(PASSED_OUTPUT, ["Bad words"], id="passed_keeps_the_turn"),
            pytest.param(STRIKE_OUTPUT, [], id="flagged_removes_the_turn"),
        ],
    )
    def test_flagged_turn_is_removed_from_pipeline_histories(
        self, moderated_session, moderation_task, output, expected_rows
    ):
        moderation_task.get.return_value = _ok(output)
        history = PipelineChatHistoryFactory.create(session=moderated_session)
        ctx = _context(moderated_session)
        PipelineChatMessagesFactory.create(chat_history=history, human_message="Bad words", ai_message="Answer")
        ctx.bot_response = _bot_response(moderated_session)

        with patch(EVENT_BOT) as event_bot:
            event_bot.return_value.get_user_message.return_value = "Flagged reply"
            ModerationResultStage()(ctx)

        assert [row.human_message for row in history.messages.all()] == expected_rows

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

        assert ctx.formatted_message == FLAGGED_MESSAGE
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

    def _send(self, session, *, process_input, message_text="Bad words"):
        channel = messaging_channel(session)
        with patch("apps.channels.stages.core.get_bot") as get_bot:
            get_bot.return_value.process_input.side_effect = process_input
            channel.new_user_message(
                base_messages.text_message(participant_id=session.participant.identifier, message_text=message_text)
            )
        self.process_input = get_bot.return_value.process_input
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

    def test_flagged_message_gets_a_text_reply_instead_of_voice(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)

        def reply_with_voice(ctx):
            ctx.formatted_message = ctx.bot_response.content
            ctx.voice_audio = MagicMock()

        with (
            patch(EVENT_BOT) as event_bot,
            patch("apps.channels.stages.core.ResponseFormattingStage.process", side_effect=reply_with_voice),
        ):
            event_bot.return_value.get_user_message.return_value = "Your message was flagged."
            channel = self._send(
                moderated_session, process_input=lambda *args, **kwargs: _bot_response(moderated_session)
            )

        assert channel.voice_sent == []
        assert channel.text_sent == ["Your message was flagged."]
        ai_message = ChatMessage.objects.get(chat=moderated_session.chat, message_type=ChatMessageType.AI)
        assert ai_message.content == "Your message was flagged."
        assert not ai_message.tags.exists()

    def test_web_channel_returns_the_saved_replacement(self, moderated_session, moderation_task):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)
        channel = WebChannel(
            experiment=moderated_session.experiment,
            experiment_channel=moderated_session.experiment_channel,
            experiment_session=moderated_session,
        )
        channel.trace_service = make_trace_service()

        with (
            patch(EVENT_BOT) as event_bot,
            patch("apps.channels.stages.core.get_bot") as get_bot,
        ):
            event_bot.return_value.get_user_message.return_value = "Your message was flagged."
            get_bot.return_value.process_input.side_effect = lambda *args, **kwargs: _bot_response(moderated_session)
            response = channel.new_user_message(
                base_messages.text_message(
                    participant_id=moderated_session.participant.identifier, message_text="Bad words"
                )
            )

        ai_message = ChatMessage.objects.get(chat=moderated_session.chat, message_type=ChatMessageType.AI)
        assert response.id == ai_message.id
        assert response.created_at == ai_message.created_at
        assert response.content == ai_message.content == "Your message was flagged."

    def test_failure_in_the_stage_still_sends_the_reply(self, moderated_session, moderation_task, caplog):
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)

        with patch(
            "apps.channels.stages.terminal.record_moderation_result", side_effect=DatabaseError("connection lost")
        ):
            channel = self._send(
                moderated_session, process_input=lambda *args, **kwargs: _bot_response(moderated_session)
            )

        assert channel.text_sent == ["The bot's answer"]
        assert "Could not record the moderation result" in caplog.text

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

    def test_flagged_message_before_consent_is_not_answered_after_consent(self, moderated_session, moderation_task):
        session = moderated_session
        session.update_status(SessionStatus.SETUP)
        session.experiment.conversational_consent_enabled = True
        session.experiment.seed_message = "Hello"
        session.experiment.save()
        moderation_task.get.return_value = _ok(STRIKE_OUTPUT)
        self._send(session, process_input=lambda *args, **kwargs: _bot_response(session))
        self.process_input.assert_not_called()

        moderation_task.get.return_value = _ok(PASSED_OUTPUT)
        self._send(session, process_input=lambda *args, **kwargs: _bot_response(session), message_text="1")

        self.process_input.assert_called_once()
        assert self.process_input.call_args.args[0] == "Hello"

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
