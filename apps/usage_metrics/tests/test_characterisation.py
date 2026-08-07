"""Characterisation tests for #3905: pin what the dashboard and the v2 usage API
compute TODAY for the same team and window, including where they diverge. The
extraction PR must keep every one of these green; only the definition-switch PR
may change an assertion here, and each change there maps to a row in the design's
divergence table.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine

from apps.api.v2.usage import services as api_usage
from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.dashboard.services import DashboardService
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

_TZ = ZoneInfo("UTC")
_START = datetime(2026, 6, 1, tzinfo=UTC)
_END = datetime(2026, 6, 15, tzinfo=UTC)
_MID = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
_BEFORE = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db()


@pytest.fixture(autouse=True)
def _frozen_time():
    """Freeze inside the window so factory-created rows (auto_now_add) land in it."""
    with time_machine.travel(_MID, tick=False):
        yield


def _backdate(session, when):
    ExperimentSession.objects.filter(pk=session.pk).update(created_at=when)
    return session


def _message(session, *, message_type=ChatMessageType.HUMAN, when=_MID):
    return ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", created_at=when)


def _overview(team):
    return DashboardService(team).get_overview_stats(start_date=_START, end_date=_END)


def _api_results(team, metrics):
    query = api_usage.resolve_query_filters(
        api_usage.UsageQuery(team=team, metrics=set(metrics), start=_START, end=_END, tz=_TZ)
    )
    return api_usage.usage_query(query).results


class TestSessionWindowDivergence:
    """Dashboard sessions = any message in the window; API sessions = created in
    the window (design section 2, row 1)."""

    def _team(self):
        # status=ACTIVE on every session here: the factory default is SETUP, which the API excludes for a
        # different reason (TestSetupSessionDivergence). Without pinning ACTIVE, `old_but_active` would be
        # dropped by the status exclusion regardless of its created_at, and this class's API assertion would
        # pass without actually exercising the created_at window filter it exists to pin.
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        old_but_active = _backdate(
            ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE), _BEFORE
        )
        _message(old_but_active)
        ExperimentSessionFactory.create(
            team=team, experiment=experiment, status=SessionStatus.ACTIVE
        )  # created in window, silent
        ExperimentSessionFactory.create(
            team=team, experiment=experiment, status=SessionStatus.ACTIVE
        )  # created in window, silent
        return team

    def test_dashboard_counts_sessions_with_message_activity_in_window(self):
        assert _overview(self._team())["total_sessions"] == 1

    def test_api_counts_sessions_created_in_window(self):
        assert _api_results(self._team(), ["sessions"])["sessions"] == 2


class TestSetupSessionDivergence:
    """The API excludes status=SETUP; the dashboard does not (design section 2, row 2)."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        setup_session = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.SETUP)
        _message(setup_session)
        return team

    def test_dashboard_counts_setup_sessions(self):
        assert _overview(self._team())["total_sessions"] == 1

    def test_api_excludes_setup_sessions(self):
        assert _api_results(self._team(), ["sessions"])["sessions"] == 0


class TestEvaluationMessageDivergence:
    """The API counts evaluation messages; the dashboard excludes them
    (design section 2, row 3)."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        eval_session = ExperimentSessionFactory.create(
            team=team,
            experiment=experiment,
            experiment_channel=ExperimentChannelFactory(
                team=team, experiment=experiment, platform=ChannelPlatform.EVALUATIONS
            ),
        )
        _message(eval_session)
        return team

    def test_dashboard_excludes_evaluation_messages(self):
        assert _overview(self._team())["total_messages"] == 0

    def test_api_counts_evaluation_messages(self):
        assert _api_results(self._team(), ["messages"])["messages"] == {"human": 1, "ai": 0, "total": 1}

    def test_api_platform_grouping_excludes_evaluations_while_total_counts_them(self):
        """The API-internal inconsistency the design's problem statement names:
        grouped rows shrink their universe, the ungrouped total does not."""
        team = self._team()
        query = api_usage.resolve_query_filters(
            api_usage.UsageQuery(
                team=team,
                metrics={"messages"},
                start=_START,
                end=_END,
                tz=_TZ,
                group_by=api_usage.GROUP_PLATFORM,
            )
        )
        assert list(api_usage.group_entities(query)) == []
        assert _api_results(team, ["messages"])["messages"]["total"] == 1


class TestMessageTypeTotalDivergence:
    """Dashboard message totals include SYSTEM messages; the API total is
    human + ai (design section 2, row 7)."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=experiment)
        _message(session, message_type=ChatMessageType.HUMAN)
        _message(session, message_type=ChatMessageType.AI)
        _message(session, message_type=ChatMessageType.SYSTEM)
        return team

    def test_dashboard_total_includes_system_messages(self):
        assert _overview(self._team())["total_messages"] == 3

    def test_api_total_is_human_plus_ai(self):
        assert _api_results(self._team(), ["messages"])["messages"] == {"human": 1, "ai": 1, "total": 2}


class TestWindowBoundaryIsHalfOpen:
    """Windows are half-open [start, end) on both surfaces (ADR-0051), so an
    instant exactly on the boundary belongs to the next period, not this one,
    and is never counted twice across adjacent periods."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(session, when=_END)
        return team

    def test_dashboard_excludes_the_end_boundary_instant(self):
        assert _overview(self._team())["total_messages"] == 0

    def test_api_excludes_the_end_boundary_instant(self):
        assert _api_results(self._team(), ["messages"])["messages"]["total"] == 0

    def test_both_surfaces_include_the_start_boundary_instant(self):
        """The other half of half-open: [start is inclusive."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(session, when=_START)

        assert _overview(team)["total_messages"] == 1
        assert _api_results(team, ["messages"])["messages"]["total"] == 1


class TestActiveParticipantsFourImplementations:
    """The four current implementations define "active participant" differently
    (design section 2, row 4): the dashboard chart counts HUMAN-message authors
    only; the dashboard session-analytics series and overview stat count
    participants with any message type (including SYSTEM-only sessions); the API
    counts HUMAN+AI. One participant per activity shape (human, AI-only,
    system-only) pins all four definitions, though this fixture only separates
    the chart and the API from the other two — session-analytics and overview
    agree here and diverge only in edge cases this fixture doesn't construct."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        human = ExperimentSessionFactory.create(team=team, experiment=experiment)
        _message(human, message_type=ChatMessageType.HUMAN)
        ai_only = ExperimentSessionFactory.create(team=team, experiment=experiment)
        _message(ai_only, message_type=ChatMessageType.AI)
        system_only = ExperimentSessionFactory.create(team=team, experiment=experiment)
        _message(system_only, message_type=ChatMessageType.SYSTEM)
        return team

    def test_dashboard_chart_counts_human_authors_only(self):
        data = DashboardService(self._team()).get_active_participants_data(
            granularity="daily", start_date=_START, end_date=_END
        )
        assert sum(point["active_participants"] for point in data) == 1

    def test_dashboard_session_analytics_counts_any_message_type(self):
        data = DashboardService(self._team()).get_session_analytics_data(
            granularity="daily", start_date=_START, end_date=_END
        )
        assert sum(point["active_participants"] for point in data["participants"]) == 3

    def test_dashboard_overview_counts_participants_of_active_sessions(self):
        assert _overview(self._team())["active_participants"] == 3

    def test_api_counts_human_and_ai_authors(self):
        assert _api_results(self._team(), ["participants"])["participants"] == 2


class TestArchivedExperimentActivity:
    """Both surfaces count archived-experiment activity; only the dashboard's
    experiment enumeration filters archived out (design section 2, row 5)."""

    def _team(self):
        team = TeamFactory.create()
        archived = ExperimentFactory.create(team=team, is_archived=True)
        # status=ACTIVE and an explicit experiment_channel tied to `archived`: the session factory's default
        # status is SETUP (a different divergence, see TestSetupSessionDivergence) and its default
        # experiment_channel builds its own unrelated Experiment via ExperimentChannelFactory's own
        # SubFactory, which would otherwise show up as a second, non-archived experiment and defeat the
        # "only one (archived) experiment exists" premise this class relies on.
        session = ExperimentSessionFactory.create(
            team=team,
            experiment=archived,
            status=SessionStatus.ACTIVE,
            experiment_channel=ExperimentChannelFactory(team=team, experiment=archived),
        )
        _message(session)
        return team

    def test_dashboard_enumeration_excludes_but_activity_includes_archived(self):
        overview = _overview(self._team())
        assert overview["total_experiments"] == 0
        assert overview["total_sessions"] == 1

    def test_api_counts_archived_experiment_activity(self):
        results = _api_results(self._team(), ["sessions", "messages"])
        assert results["sessions"] == 1
        assert results["messages"]["total"] == 1
