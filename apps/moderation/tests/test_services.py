from unittest import mock

import pytest
from celery.exceptions import TimeoutError as CeleryTimeoutError

from apps.chat.models import ChatMessage, ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.moderation.const import MODERATION_WAIT_TIMEOUT
from apps.moderation.services import (
    get_moderation_result,
    record_moderation_result,
    record_rule_hits,
    start_moderation,
    strike_categories,
)
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory
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
        pytest.param(True, True, None, False, id="no_text"),
        pytest.param(True, True, "  ", False, id="whitespace"),
        pytest.param(True, True, EMPTY_MESSAGE_PLACEHOLDER, False, id="empty_message_placeholder"),
    ],
)
def test_start_moderation(moderation_enabled, has_provider, text, started):
    team = TeamFactory.create(moderation_enabled=moderation_enabled)
    if has_provider:
        team.moderation_provider = ModerationProviderFactory.create(team=team)
        team.save()

    with mock.patch("apps.moderation.services.moderate_message") as task:
        task.delay.return_value.id = "task-1"
        task_id = start_moderation(team=team, text=text)

    assert task_id == ("task-1" if started else None)
    if started:
        task.delay.assert_called_once_with(text=text, moderation_provider_id=team.moderation_provider_id)
    else:
        task.delay.assert_not_called()


@pytest.mark.parametrize(
    ("wait", "expected"),
    [
        pytest.param(
            {"return_value": {"status": "ok", "output": {"hate": {"flagged": False, "score": 0.1}}}},
            (ModerationOutput({"hate": ModerationCategory(flagged=False, score=0.1)}), None),
            id="ok",
        ),
        pytest.param(
            {"return_value": {"status": "skipped", "reason": "rate_limited"}}, (None, "rate_limited"), id="skipped"
        ),
        pytest.param({"side_effect": CeleryTimeoutError()}, (None, "timeout"), id="timeout"),
        pytest.param({"side_effect": RuntimeError("boom")}, (None, "error"), id="task_raised"),
        pytest.param(
            {"return_value": {"status": "ok", "output": {"hate": {"flagged": True, "score": 2}}}},
            (None, "error"),
            id="invalid_output",
        ),
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
