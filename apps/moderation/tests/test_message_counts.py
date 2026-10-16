from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.chat.models import ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.moderation.services import ModerationCounts, count_moderated_messages
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory
from apps.utils.factories.team import TeamFactory


def _message(*, chat, status=None, skip_reason=None, rules=(), age=timedelta(hours=1)):
    detail = None
    if skip_reason or rules:
        detail = ModerationDetail(skip_reason=skip_reason, rules=list(rules))
    return ChatMessageFactory.create(
        chat=chat,
        message_type=ChatMessageType.HUMAN,
        content="hi",
        moderation_status=status,
        moderation_detail=detail,
        created_at=timezone.now() - age,
    )


@pytest.mark.django_db()
def test_counts_messages_by_status_and_skip_reason():
    chat = ChatFactory.create()
    for _ in range(3):
        _message(chat=chat, status=ModerationStatus.PASSED)
    _message(chat=chat, status=ModerationStatus.FLAGGED, rules=["markup"])
    for _ in range(2):
        _message(chat=chat, status=ModerationStatus.SKIPPED, skip_reason="rate_limited")
    _message(chat=chat, status=ModerationStatus.SKIPPED, skip_reason="timeout")
    _message(chat=chat, status=None, rules=["markup"])
    _message(chat=chat, status=ModerationStatus.FLAGGED, age=timedelta(days=8))
    _message(chat=ChatFactory.create(), status=ModerationStatus.FLAGGED)

    counts = count_moderated_messages(team=chat.team, since=timezone.now() - timedelta(days=7))

    assert counts == ModerationCounts(passed=3, flagged=1, skipped=3, skip_reasons={"rate_limited": 2, "timeout": 1})
    assert counts.total == 7


@pytest.mark.django_db()
def test_counts_with_no_moderated_messages():
    counts = count_moderated_messages(team=TeamFactory.create(), since=timezone.now() - timedelta(days=7))

    assert counts == ModerationCounts()
    assert counts.total == 0
    assert not counts.mostly_rate_limited


@pytest.mark.django_db()
def test_counts_use_one_query():
    chat = ChatFactory.create()
    _message(chat=chat, status=ModerationStatus.PASSED)
    _message(chat=chat, status=ModerationStatus.SKIPPED, skip_reason="error")

    with CaptureQueriesContext(connection) as queries:
        count_moderated_messages(team=chat.team, since=timezone.now() - timedelta(days=7))

    assert len(queries.captured_queries) == 1


@pytest.mark.parametrize(
    ("skip_reasons", "skipped", "expected"),
    [
        pytest.param({"rate_limited": 3, "timeout": 1}, 4, True, id="most-rate-limited"),
        pytest.param({"rate_limited": 2, "timeout": 2}, 4, False, id="half-rate-limited"),
        pytest.param({"error": 4}, 4, False, id="no-rate-limits"),
        pytest.param({}, 0, False, id="nothing-skipped"),
    ],
)
def test_mostly_rate_limited(skip_reasons, skipped, expected):
    counts = ModerationCounts(skipped=skipped, skip_reasons=skip_reasons)

    assert counts.mostly_rate_limited is expected
