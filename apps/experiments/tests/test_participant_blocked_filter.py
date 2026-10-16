"""Tests for the blocked filter on the session list and participant table views."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from waffle.testutils import override_flag

from apps.experiments.filters import ExperimentSessionFilter
from apps.participants.filters import ParticipantFilter
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.web.dynamic_filters.base import Operators


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
def test_blocked_filter_offered_only_when_the_flag_is_on(db, team, filter_class, query_param, flag_active, expected):
    with override_flag(Flags.ABUSE_DETECTION.slug, active=flag_active):
        columns = filter_class.columns(team)

    assert (query_param in columns) is expected


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
