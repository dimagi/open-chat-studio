"""Tests for the "Participant Blocked" filter on the session list and the "Blocked" filter on the participant table."""

from types import SimpleNamespace

import pytest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from waffle.testutils import override_flag

from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession, Participant
from apps.participants.filters import ParticipantFilter
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.team import TeamFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


def _apply(*, filter_class, queryset, query_param, value, operator, flag_active=True):
    query_dict = QueryDict("", mutable=True)
    query_dict.update({f"f_{query_param}": value, f"op_{query_param}": operator})
    with override_flag(Flags.ABUSE_DETECTION.slug, active=flag_active):
        return filter_class().apply(queryset, filter_params=FilterParams(query_dict))


@pytest.fixture()
def sessions(db):
    """Sessions whose participant is blocked, not blocked, or blocked only on another team."""
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)
    blocked = ExperimentSessionFactory.create(experiment=experiment)
    DeniedParticipantFactory.create(team=team, participant=blocked.participant)
    # A second session for the blocked participant, so a JOIN-based filter would duplicate rows.
    blocked_again = ExperimentSessionFactory.create(experiment=experiment, participant=blocked.participant)
    not_blocked = ExperimentSessionFactory.create(experiment=experiment)
    other_team_block = ExperimentSessionFactory.create(experiment=experiment)
    # The denylist row names this participant but belongs to a different team, so it does not block them here.
    DeniedParticipantFactory.create(team=TeamFactory.create(), participant=other_team_block.participant)
    return SimpleNamespace(
        team=team,
        blocked=blocked,
        blocked_again=blocked_again,
        not_blocked=not_blocked,
        other_team_block=other_team_block,
    )


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param(Operators.ANY_OF, "true", ["blocked", "blocked_again"], id="any-of-blocked"),
        pytest.param(Operators.ANY_OF, "false", ["not_blocked", "other_team_block"], id="any-of-not-blocked"),
        pytest.param(
            Operators.ANY_OF,
            "true~false",
            ["blocked", "blocked_again", "not_blocked", "other_team_block"],
            id="any-of-both",
        ),
        pytest.param(Operators.EXCLUDES, "true", ["not_blocked", "other_team_block"], id="excludes-blocked"),
        pytest.param(Operators.EXCLUDES, "false", ["blocked", "blocked_again"], id="excludes-not-blocked"),
        pytest.param(
            Operators.ALL_OF,
            "true",
            ["blocked", "blocked_again", "not_blocked", "other_team_block"],
            id="all-of-has-no-effect",
        ),
    ],
)
def test_session_participant_blocked_filter(sessions, operator, value, expected):
    result = _apply(
        filter_class=ExperimentSessionFilter,
        queryset=ExperimentSession.objects.filter(team=sessions.team),
        query_param="participant_blocked",
        value=value,
        operator=operator,
    )

    assert sorted(result.values_list("id", flat=True)) == sorted(getattr(sessions, name).id for name in expected)


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param(Operators.ANY_OF, "true", ["blocked"], id="any-of-blocked"),
        pytest.param(Operators.ANY_OF, "false", ["not_blocked", "other_team_block"], id="any-of-not-blocked"),
        pytest.param(Operators.EXCLUDES, "true", ["not_blocked", "other_team_block"], id="excludes-blocked"),
        pytest.param(Operators.EXCLUDES, "false", ["blocked"], id="excludes-not-blocked"),
        pytest.param(
            Operators.ALL_OF, "true", ["blocked", "not_blocked", "other_team_block"], id="all-of-has-no-effect"
        ),
    ],
)
def test_participant_blocked_filter(sessions, operator, value, expected):
    result = _apply(
        filter_class=ParticipantFilter,
        queryset=Participant.objects.filter(team=sessions.team),
        query_param="blocked",
        value=value,
        operator=operator,
    )

    expected_ids = sorted(getattr(sessions, name).participant_id for name in expected)
    assert sorted(result.values_list("id", flat=True)) == expected_ids


@pytest.mark.parametrize(
    ("filter_class", "query_param"),
    [
        pytest.param(ExperimentSessionFilter, "participant_blocked", id="sessions"),
        pytest.param(ParticipantFilter, "blocked", id="participants"),
    ],
)
@pytest.mark.parametrize(
    ("flag_active", "expected"),
    [pytest.param(True, True, id="flag-on"), pytest.param(False, False, id="flag-off")],
)
def test_blocked_filter_offered_only_when_the_flag_is_on(sessions, filter_class, query_param, flag_active, expected):
    with override_flag(Flags.ABUSE_DETECTION.slug, active=flag_active):
        columns = filter_class.columns(sessions.team)

    assert (query_param in columns) is expected


def test_blocked_filter_still_applies_when_the_flag_is_off(sessions):
    """Saved filters keep filtering after the flag is turned off."""
    result = _apply(
        filter_class=ExperimentSessionFilter,
        queryset=ExperimentSession.objects.filter(team=sessions.team),
        query_param="participant_blocked",
        value="true",
        operator=Operators.ANY_OF,
        flag_active=False,
    )

    assert sorted(result.values_list("id", flat=True)) == sorted([sessions.blocked.id, sessions.blocked_again.id])


@pytest.mark.parametrize(
    ("viewname", "query_param"),
    [
        pytest.param("chatbots:all_sessions_list", "participant_blocked", id="sessions"),
        pytest.param("participants:participant_table", "blocked", id="participants"),
    ],
)
def test_table_ignores_an_operator_the_filter_does_not_offer(client, team_with_users, viewname, query_param):
    client.force_login(team_with_users.members.first())
    url = reverse(viewname=viewname, kwargs={"team_slug": team_with_users.slug})
    query = {"filter_0_column": query_param, "filter_0_operator": Operators.ALL_OF, "filter_0_value": '["true"]'}

    with override_flag(Flags.ABUSE_DETECTION.slug, active=True):
        response = client.get(url, data=query)

    assert response.status_code == 200


BLOCKED_FILTER_QUERY = {
    "filter_0_column": "participant_blocked",
    "filter_0_operator": Operators.ANY_OF,
    "filter_0_value": '["true"]',
}


def _session_list_queries(*, client, url, query):
    """The number of queries the session list runs, and the sessions it shows."""
    with override_flag(Flags.ABUSE_DETECTION.slug, active=True), CaptureQueriesContext(connection) as queries:
        response = client.get(url, data=query)
    assert response.status_code == 200
    return len(queries), len(response.context_data["table"].data.data)


@pytest.mark.parametrize("session_count", [pytest.param(1, id="one-session"), pytest.param(4, id="four-sessions")])
def test_blocked_filter_adds_no_queries_to_the_session_list(client, team_with_users, session_count):
    """Every session is blocked, so the filtered and unfiltered lists show the same rows."""
    team = team_with_users
    client.force_login(team.members.first())
    experiment = ExperimentFactory.create(team=team)
    for _ in range(session_count):
        session = ExperimentSessionFactory.create(experiment=experiment)
        DeniedParticipantFactory.create(team=team, participant=session.participant)
    url = reverse(viewname="chatbots:all_sessions_list", kwargs={"team_slug": team.slug})
    _session_list_queries(client=client, url=url, query={})  # warm per-process caches such as content types

    unfiltered = _session_list_queries(client=client, url=url, query={})
    filtered = _session_list_queries(client=client, url=url, query=BLOCKED_FILTER_QUERY)

    assert filtered == unfiltered == (unfiltered[0], session_count)
