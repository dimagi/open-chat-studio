"""Cross-surface definition tests for #3905: the dashboard and the v2 usage API
compute the same activity metrics the same way (ADR-0051). Each class here
covers one row of the design's former divergence table, asserting the converged
behaviour on both surfaces. These started life as characterisation tests pinning
the divergence; they now pin its absence.
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
from apps.usage_metrics import metrics
from apps.usage_metrics.filters import UsageFilters
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


class TestSessionMetricsAreTwoNamedDefinitions:
    """`sessions_active` (a conversation turn in the window) and
    `sessions_started` (created in the window) are two different questions, so
    they legitimately differ - but each is now computed one way, and each
    surface labels which one it shows (ADR-0051). The dashboard shows active;
    the API's `sessions` metric is started."""

    def _team(self):
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

    def test_dashboard_shows_sessions_active(self):
        assert _overview(self._team())["total_sessions"] == 1

    def test_api_shows_sessions_started(self):
        assert _api_results(self._team(), ["sessions"])["sessions"] == 2


class TestSetupSessionsCountOnNeitherSurface:
    """A session created but never engaged is not active and was not started
    (ADR-0051); `sessions_in_setup` is where it stays countable."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        setup_session = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.SETUP)
        _message(setup_session)
        return team

    def test_dashboard_excludes_setup_sessions(self):
        assert _overview(self._team())["total_sessions"] == 0

    def test_api_excludes_setup_sessions(self):
        assert _api_results(self._team(), ["sessions"])["sessions"] == 0

    def test_setup_sessions_stay_countable_as_sessions_in_setup(self):
        assert metrics.sessions_in_setup(self._team(), start=_START, end=_END, filters=UsageFilters()) == 1


class TestSetupSessionActivityCountsNowhere:
    """A session still in `SETUP` was never engaged, so nothing inside it
    counts on any metric (ADR-0051) - not its turns, not its author. Counting
    its messages while `sessions_active` drops the session would put a ratio's
    numerator and denominator on different universes, which is exactly what
    the ADR's ratios rule forbids."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        in_setup = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.SETUP)
        _message(in_setup, message_type=ChatMessageType.HUMAN)
        _message(in_setup, message_type=ChatMessageType.AI)
        return team

    def test_dashboard_messages_exclude_setup_session_turns(self):
        assert _overview(self._team())["total_messages"] == 0

    def test_dashboard_participants_exclude_setup_session_authors(self):
        assert _overview(self._team())["active_participants"] == 0

    def test_api_messages_exclude_setup_session_turns(self):
        assert _api_results(self._team(), ["messages"])["messages"] == {"human": 0, "ai": 0, "total": 0}

    def test_api_participants_exclude_setup_session_authors(self):
        assert _api_results(self._team(), ["participants"])["participants"] == 0


class TestSessionsActiveNeedsAConversationTurn:
    """A session whose only in-window activity is a `system` message was not
    active (ADR-0051)."""

    def test_system_only_session_is_not_active(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(session, message_type=ChatMessageType.SYSTEM)

        assert _overview(team)["total_sessions"] == 0


class TestEvaluationActivityIsExcludedEverywhere:
    """Evaluation-harness activity counts on neither surface (ADR-0051), and
    the API's grouped rows now sum to its ungrouped total."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        eval_session = ExperimentSessionFactory.create(
            team=team,
            experiment=experiment,
            status=SessionStatus.ACTIVE,
            experiment_channel=ExperimentChannelFactory(
                team=team, experiment=experiment, platform=ChannelPlatform.EVALUATIONS
            ),
        )
        _message(eval_session)
        return team

    def test_dashboard_excludes_evaluation_messages(self):
        assert _overview(self._team())["total_messages"] == 0

    def test_api_excludes_evaluation_messages(self):
        assert _api_results(self._team(), ["messages"])["messages"] == {"human": 0, "ai": 0, "total": 0}

    def test_api_excludes_evaluation_participants(self):
        assert _api_results(self._team(), ["participants"])["participants"] == 0

    def test_api_platform_grouping_and_total_agree(self):
        """The API-internal inconsistency the design's problem statement named:
        grouped rows shrank their universe while the ungrouped total did not.
        Both now exclude evaluations, so the two reconcile."""
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
        assert _api_results(team, ["messages"])["messages"]["total"] == 0


class TestMessageTotalIsHumanPlusAi:
    """`system` messages are internal and are not conversation turns, so both
    surfaces count human + ai (ADR-0051)."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(session, message_type=ChatMessageType.HUMAN)
        _message(session, message_type=ChatMessageType.AI)
        _message(session, message_type=ChatMessageType.SYSTEM)
        return team

    def test_dashboard_total_excludes_system_messages(self):
        assert _overview(self._team())["total_messages"] == 2

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


class TestActiveParticipantsIsOneDefinition:
    """What was four implementations is one (ADR-0051): a participant is active
    when they authored a HUMAN message in the window. Receiving AI output is
    not activity, and neither is a `system` message. One participant per
    activity shape pins every surface to the same answer."""

    def _team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        human = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(human, message_type=ChatMessageType.HUMAN)
        ai_only = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(ai_only, message_type=ChatMessageType.AI)
        system_only = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(system_only, message_type=ChatMessageType.SYSTEM)
        return team

    def test_dashboard_chart_counts_human_authors(self):
        data = DashboardService(self._team()).get_active_participants_data(
            granularity="daily", start_date=_START, end_date=_END
        )
        assert sum(point["active_participants"] for point in data) == 1

    def test_dashboard_session_analytics_counts_human_authors(self):
        data = DashboardService(self._team()).get_session_analytics_data(
            granularity="daily", start_date=_START, end_date=_END
        )
        assert sum(point["active_participants"] for point in data["participants"]) == 1

    def test_dashboard_overview_counts_human_authors(self):
        assert _overview(self._team())["active_participants"] == 1

    def test_api_counts_human_authors(self):
        assert _api_results(self._team(), ["participants"])["participants"] == 1


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
