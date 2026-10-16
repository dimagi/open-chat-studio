"""Tests for the session list's moderation status and rule hit filters."""

from types import SimpleNamespace

import pytest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from waffle.testutils import override_flag

from apps.chat.models import ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


def _filter_sessions(*, team, query_param, value, operator):
    query_dict = QueryDict("", mutable=True)
    query_dict.update({f"f_{query_param}": value, f"op_{query_param}": operator})
    queryset = ExperimentSession.objects.filter(team=team)
    return ExperimentSessionFilter().apply(queryset, FilterParams(query_dict))


def _session_with_messages(*, team, messages):
    """A session with one human message per (moderation_status, rules) pair, plus an AI reply to each."""
    session = ExperimentSessionFactory.create(team=team, chat__team=team, experiment__team=team)
    for status, rules in messages:
        ChatMessageFactory.create(
            chat=session.chat,
            message_type=ChatMessageType.HUMAN,
            content="hi",
            moderation_status=status,
            moderation_detail=ModerationDetail(rules=rules) if rules is not None else None,
        )
        ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.AI, content="hello")
    return session


@pytest.fixture()
def sessions(db):
    team = TeamFactory.create()
    return SimpleNamespace(
        team=team,
        flagged=_session_with_messages(
            team=team, messages=[(ModerationStatus.PASSED, None), (ModerationStatus.FLAGGED, None)]
        ),
        # Two matching messages, so a JOIN-based filter would return this session twice.
        skipped=_session_with_messages(
            team=team, messages=[(ModerationStatus.SKIPPED, ["markup"]), (ModerationStatus.SKIPPED, ["markup"])]
        ),
        passed=_session_with_messages(team=team, messages=[(ModerationStatus.PASSED, [])]),
        unmoderated=_session_with_messages(team=team, messages=[(None, ["markup"]), (None, None)]),
    )


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param(Operators.ANY_OF, "flagged", ["flagged"], id="any-of-flagged"),
        pytest.param(Operators.ANY_OF, "passed", ["flagged", "passed"], id="any-of-passed"),
        pytest.param(Operators.ANY_OF, "skipped", ["skipped"], id="any-of-skipped"),
        pytest.param(Operators.ANY_OF, "flagged~skipped", ["flagged", "skipped"], id="any-of-two"),
        pytest.param(Operators.EXCLUDES, "flagged", ["skipped", "passed", "unmoderated"], id="excludes-flagged"),
        pytest.param(Operators.EXCLUDES, "passed~skipped", ["unmoderated"], id="excludes-two"),
    ],
)
def test_moderation_status_filter(sessions, operator, value, expected):
    result = _filter_sessions(team=sessions.team, query_param="moderation_status", value=value, operator=operator)

    assert sorted(result.values_list("id", flat=True)) == sorted(getattr(sessions, name).id for name in expected)


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param(Operators.ANY_OF, "true", ["skipped", "unmoderated"], id="any-of-rule-fired"),
        pytest.param(Operators.ANY_OF, "false", ["flagged", "passed"], id="any-of-no-rule-fired"),
        pytest.param(Operators.ANY_OF, "true~false", ["flagged", "skipped", "passed", "unmoderated"], id="any-of-both"),
        pytest.param(Operators.EXCLUDES, "true", ["flagged", "passed"], id="excludes-rule-fired"),
        pytest.param(
            Operators.EXCLUDES, "true~false", ["flagged", "skipped", "passed", "unmoderated"], id="excludes-both"
        ),
    ],
)
def test_rule_hit_filter(sessions, operator, value, expected):
    result = _filter_sessions(team=sessions.team, query_param="rule_hit", value=value, operator=operator)

    assert sorted(result.values_list("id", flat=True)) == sorted(getattr(sessions, name).id for name in expected)


@pytest.mark.parametrize(
    ("query_param", "value"),
    [
        pytest.param("moderation_status", "flagged~skipped", id="moderation-status"),
        pytest.param("rule_hit", "true", id="rule-hit"),
    ],
)
def test_filters_use_one_exists_subquery(sessions, query_param, value):
    """The filters use EXISTS, so the page query is a single query with no join to the messages."""
    result = _filter_sessions(team=sessions.team, query_param=query_param, value=value, operator=Operators.ANY_OF)

    with CaptureQueriesContext(connection) as queries:
        list(result)

    assert len(queries.captured_queries) == 1
    assert "EXISTS" in queries.captured_queries[0]["sql"]


@pytest.mark.parametrize(
    ("flag_active", "expected"),
    [pytest.param(True, True, id="flag-on"), pytest.param(False, False, id="flag-off")],
)
def test_moderation_filters_offered_only_when_the_flag_is_on(sessions, flag_active, expected):
    with override_flag(Flags.ABUSE_DETECTION.slug, active=flag_active):
        columns = ExperimentSessionFilter.columns(sessions.team)

    assert ("moderation_status" in columns) is expected
    assert ("rule_hit" in columns) is expected
