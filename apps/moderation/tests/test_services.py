from unittest import mock

import pytest
from celery.exceptions import TimeoutError as CeleryTimeoutError
from django.db import DatabaseError

from apps.chat.models import ChatMessage, ChatMessageMetadataKeys, ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.moderation.const import MODERATION_WAIT_TIMEOUT
from apps.moderation.services import (
    get_moderation_result,
    record_moderation_result,
    record_rule_hits,
    remove_turn_from_pipeline_histories,
    replace_bot_reply,
    start_moderation,
    strike_categories,
)
from apps.moderation.tasks import moderate_message
from apps.pipelines.models import PipelineChatHistoryModes, PipelineChatHistoryTypes
from apps.pipelines.repository import ORMRepository
from apps.service_providers.moderation_service import ModerationRateLimited
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineChatHistoryFactory, PipelineChatMessagesFactory
from apps.utils.factories.service_provider_factories import ModerationProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.llm_messages import EMPTY_MESSAGE_PLACEHOLDER

OUTPUT = ModerationOutput({"harassment": ModerationCategory(flagged=False, score=0.1)})


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("text", "initial", "expected"),
    [
        pytest.param("hello", None, None, id="no_hit"),
        pytest.param("<script>", None, ModerationDetail(rules=["markup"]), id="hit"),
        pytest.param(
            "<script>",
            ModerationDetail(output=OUTPUT),
            ModerationDetail(output=OUTPUT, rules=["markup"]),
            id="hit_keeps_existing_detail",
        ),
    ],
)
def test_record_rule_hits(text, initial, expected):
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content=text, moderation_detail=initial
    )

    record_rule_hits(message=message, text=text, attachments=[], participant=None)

    message.refresh_from_db()
    assert message.moderation_detail == expected


@pytest.mark.django_db()
def test_record_rule_hits_updates_updated_at():
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content="<script>"
    )
    before = message.updated_at

    record_rule_hits(message=message, text="<script>", attachments=[], participant=None)

    message.refresh_from_db()
    assert message.updated_at > before


def _output(flags: dict[str, bool]) -> ModerationOutput:
    """An output that flags the categories set to True."""
    return ModerationOutput(
        {name: ModerationCategory(flagged=flagged, score=0.9 if flagged else 0.1) for name, flagged in flags.items()}
    )


def _human_message(detail: ModerationDetail | None = None):
    return ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content="text", moderation_detail=detail
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("output", "skip_reason", "expected_status"),
    [
        pytest.param(_output({"harassment": False, "illicit": False}), None, ModerationStatus.PASSED, id="passed"),
        pytest.param(
            _output({"harassment": True, "illicit": False}), None, ModerationStatus.PASSED, id="non_strike_flagged"
        ),
        pytest.param(
            _output({"harassment": True, "illicit": True}), None, ModerationStatus.FLAGGED, id="strike_flagged"
        ),
        pytest.param(None, "timeout", ModerationStatus.SKIPPED, id="skipped"),
    ],
)
def test_record_moderation_result(output, skip_reason, expected_status):
    message = _human_message()

    status = record_moderation_result(message=message, output=output, skip_reason=skip_reason)

    message.refresh_from_db()
    assert status == expected_status
    assert message.moderation_status == expected_status
    assert message.moderation_detail == ModerationDetail(output=output, skip_reason=skip_reason)


@pytest.mark.django_db()
def test_record_moderation_result_keeps_rules_and_updates_updated_at():
    message = _human_message(ModerationDetail(rules=["markup"]))
    before = message.updated_at

    record_moderation_result(message=message, output=OUTPUT)

    message.refresh_from_db()
    assert message.moderation_detail == ModerationDetail(output=OUTPUT, rules=["markup"])
    assert message.updated_at > before


@pytest.mark.parametrize(
    ("output", "skip_reason"),
    [
        pytest.param(None, None, id="neither"),
        pytest.param(OUTPUT, "error", id="both"),
    ],
)
def test_record_moderation_result_needs_exactly_one_outcome(output, skip_reason):
    with pytest.raises(ValueError, match="exactly one"):
        record_moderation_result(message=ChatMessage(), output=output, skip_reason=skip_reason)


def test_strike_categories():
    assert strike_categories(_output({"harassment": True, "illicit": True, "sexual/minors": False})) == {"illicit"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("moderation_enabled", "has_provider", "text", "started"),
    [
        pytest.param(True, True, "hello", True, id="started"),
        pytest.param(False, True, "hello", False, id="moderation_off"),
        pytest.param(True, False, "hello", False, id="no_provider"),
        pytest.param(True, True, "", False, id="empty_text"),
        pytest.param(True, True, "  ", False, id="whitespace"),
        pytest.param(True, True, EMPTY_MESSAGE_PLACEHOLDER, False, id="empty_message_placeholder"),
    ],
)
def test_start_moderation(moderation_enabled, has_provider, text, started):
    team = TeamFactory.create(moderation_enabled=moderation_enabled)
    if has_provider:
        team.moderation_provider = ModerationProviderFactory.create(team=team)
        team.save()
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(team=team), message_type=ChatMessageType.HUMAN, content=text or ""
    )

    with mock.patch("apps.moderation.services.moderate_message") as task:
        task.delay.return_value.id = "task-1"
        task_id = start_moderation(team=team, message=message)

    assert task_id == ("task-1" if started else None)
    if started:
        task.delay.assert_called_once_with(
            chat_message_id=message.id, moderation_provider_id=team.moderation_provider_id
        )
    else:
        task.delay.assert_not_called()


@pytest.mark.parametrize(
    ("wait", "expected"),
    [
        pytest.param(
            {"return_value": {"output": {"hate": {"flagged": False, "score": 0.1}}, "skip_reason": None}},
            (ModerationOutput({"hate": ModerationCategory(flagged=False, score=0.1)}), None),
            id="ok",
        ),
        pytest.param(
            {"return_value": {"output": None, "skip_reason": "rate_limited"}}, (None, "rate_limited"), id="skipped"
        ),
        pytest.param({"side_effect": CeleryTimeoutError()}, (None, "timeout"), id="timeout"),
        pytest.param({"side_effect": RuntimeError("boom")}, (None, "error"), id="task_raised"),
        pytest.param(
            {"return_value": {"output": {"hate": {"flagged": True, "score": 2}}, "skip_reason": None}},
            (None, "error"),
            id="invalid_output",
        ),
        pytest.param(
            {"return_value": {"output": None, "skip_reason": "unknown"}}, (None, "error"), id="unknown_skip_reason"
        ),
        pytest.param({"return_value": {"status": "ok"}}, (None, "error"), id="unexpected_shape"),
    ],
)
def test_get_moderation_result(wait, expected):
    with mock.patch("apps.moderation.services.AsyncResult") as async_result:
        async_result.return_value.get.configure_mock(**wait)
        result = get_moderation_result("task-1")

    assert result == expected
    async_result.assert_called_once_with("task-1")
    async_result.return_value.get.assert_called_once_with(
        timeout=MODERATION_WAIT_TIMEOUT, disable_sync_subtasks=False, interval=0.1
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("moderate", "expected"),
    [
        pytest.param({"return_value": OUTPUT}, (OUTPUT, None), id="output"),
        pytest.param({"side_effect": ModerationRateLimited("429")}, (None, "rate_limited"), id="skipped"),
    ],
)
def test_get_moderation_result_reads_the_task_result(moderate, expected):
    message = _human_message()
    provider = ModerationProviderFactory.create()
    with mock.patch("apps.service_providers.moderation_service.OpenAIModerationService.moderate", **moderate):
        task_result = moderate_message.apply(
            kwargs={"chat_message_id": message.id, "moderation_provider_id": provider.id}
        ).get()

    with mock.patch("apps.moderation.services.AsyncResult") as async_result:
        async_result.return_value.get.return_value = task_result
        assert get_moderation_result("task-1") == expected


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "history_type",
    [
        pytest.param(PipelineChatHistoryTypes.NODE, id="node_history"),
        pytest.param(PipelineChatHistoryTypes.NAMED, id="named_history"),
    ],
)
def test_remove_turn_from_pipeline_histories(history_type):
    session = ExperimentSessionFactory.create()
    other_session = ExperimentSessionFactory.create()
    history = PipelineChatHistoryFactory.create(session=session, type=history_type, name="history")
    other_history = PipelineChatHistoryFactory.create(session=other_session, type=history_type, name="history")
    PipelineChatMessagesFactory.create(chat_history=history, human_message="Earlier", ai_message="Earlier answer")
    human_message = ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Bad")
    PipelineChatMessagesFactory.create(chat_history=history, human_message="Bad", ai_message="Original answer")
    PipelineChatMessagesFactory.create(chat_history=other_history, human_message="Other", ai_message="Other answer")
    bot_response = ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.AI, content="Answer")

    remove_turn_from_pipeline_histories(human_message=human_message, bot_response=bot_response)

    node_history = ORMRepository(session=session).get_pipeline_chat_history(history_type, "history")
    messages = node_history.get_langchain_messages_until_marker(PipelineChatHistoryModes.TRUNCATE_TOKENS)
    assert [message.content for message in messages] == ["Earlier", "Earlier answer"]
    assert other_history.messages.count() == 1


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(CeleryTimeoutError(), id="timeout"),
        pytest.param(RuntimeError("boom"), id="task_raised"),
    ],
)
def test_get_moderation_result_logs_a_failed_task_as_a_warning(error, caplog):
    with mock.patch("apps.moderation.services.AsyncResult") as async_result:
        async_result.return_value.get.side_effect = error
        get_moderation_result("task-1")

    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert caplog.records[0].exc_info is None


def _bot_reply():
    return ChatMessageFactory.create(
        chat=ChatFactory.create(),
        message_type=ChatMessageType.AI,
        content="The bot's answer",
        metadata={ChatMessageMetadataKeys.OCS_ATTACHMENT_FILE_IDS: [1], "trace_info": {"trace_id": "t-1"}},
    )


@pytest.mark.django_db()
def test_replace_bot_reply_drops_attachments():
    message = _bot_reply()

    replace_bot_reply(message=message, text="Flagged")

    message.refresh_from_db()
    assert message.content == "Flagged"
    assert message.metadata == {"trace_info": {"trace_id": "t-1"}}


@pytest.mark.django_db()
def test_replace_bot_reply_restores_the_message_when_the_save_fails():
    message = _bot_reply()
    original_metadata = message.metadata

    with (
        mock.patch.object(message, "save", side_effect=DatabaseError("connection lost")),
        pytest.raises(DatabaseError),
    ):
        replace_bot_reply(message=message, text="Flagged")

    assert message.content == "The bot's answer"
    assert message.metadata == original_metadata
