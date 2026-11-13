"""Each usage surface equals `usage_metrics` under its own parameterisation,
and the two surfaces equal each other where their parameters genuinely coincide
- no tag filter, identical half-open windows, the same timezone, and the same
question asked of each (the dashboard shows `sessions_active`, the API's
`sessions` metric is `sessions_started`, so those two are compared to their own
metric function, not to one another). The properties of each definition are
pinned once, in `test_metrics.py`; the tests here are the standing guard
against a surface drifting off the shared definition again (#3905, ADR-0051).
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.utils import timezone

from apps.api.v2.usage import services as api_usage
from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.dashboard.services import DashboardService
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.usage_metrics import metrics
from apps.usage_metrics.buckets import Granularity
from apps.usage_metrics.filters import UsageFilters
from apps.utils.factories.annotations import CustomTaggedItemFactory, TagFactory
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
    with time_machine.travel(_MID, tick=False):
        yield


def _message(session, *, message_type=ChatMessageType.HUMAN, when=_MID):
    return ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", created_at=when)


@pytest.fixture()
def busy_team():
    """One team carrying every activity shape the definitions discriminate on,
    so a single fixture exercises each metric's exclusions at once."""
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)

    started_and_active = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
    _message(started_and_active, message_type=ChatMessageType.HUMAN)
    _message(started_and_active, message_type=ChatMessageType.AI)

    # Started before the window, active inside it: counts as active, not as started.
    old_but_active = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
    ExperimentSession.objects.filter(pk=old_but_active.pk).update(created_at=_BEFORE)
    _message(old_but_active, message_type=ChatMessageType.HUMAN)

    # Started inside the window, never engaged: counts as started, not as active.
    ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)

    # Never left setup: counts as neither.
    in_setup = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.SETUP)
    _message(in_setup, message_type=ChatMessageType.HUMAN)

    # System-only traffic: not a conversation turn on any metric, but still a
    # session created (and out of SETUP) in the window, so it counts as started.
    system_only = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
    _message(system_only, message_type=ChatMessageType.SYSTEM)

    return team


def _overview(team):
    return DashboardService(team).get_overview_stats(start_date=_START, end_date=_END)


def _api_results(team, metric_names, *, granularity=api_usage.GRANULARITY_TOTAL, tz=_TZ):
    query = api_usage.resolve_query_filters(
        api_usage.UsageQuery(
            team=team, metrics=list(metric_names), start=_START, end=_END, tz=tz, granularity=granularity
        )
    )
    return api_usage.usage_query(query).results


class TestDashboardEqualsUsageMetrics:
    def test_overview_sessions_equal_sessions_active(self, busy_team):
        expected = metrics.sessions_active(busy_team, start=_START, end=_END, filters=UsageFilters())

        assert _overview(busy_team)["total_sessions"] == expected

    def test_overview_messages_equal_the_messages_total(self, busy_team):
        expected = metrics.messages(busy_team, start=_START, end=_END, filters=UsageFilters())["total"]

        assert _overview(busy_team)["total_messages"] == expected

    def test_overview_participants_equal_active_participants(self, busy_team):
        expected = metrics.active_participants(busy_team, start=_START, end=_END, filters=UsageFilters())

        assert _overview(busy_team)["active_participants"] == expected

    @pytest.mark.parametrize(
        ("series", "value_key", "scalar"),
        [
            pytest.param("sessions", "active_sessions", metrics.sessions_active, id="sessions"),
            pytest.param("participants", "active_participants", metrics.active_participants, id="participants"),
        ],
    )
    def test_session_analytics_series_sum_to_their_scalars(self, busy_team, series, value_key, scalar):
        """No session in the fixture spans two days, so each per-bucket series
        sums to its scalar. A session active across several buckets would count
        once per bucket by design, which is why this is asserted on a fixture
        that cannot exercise that case."""
        data = DashboardService(busy_team).get_session_analytics_data(
            granularity="daily", start_date=_START, end_date=_END
        )
        expected = scalar(busy_team, start=_START, end=_END, filters=UsageFilters())

        assert sum(point[value_key] for point in data[series]) == expected


class TestApiEqualsUsageMetrics:
    def test_api_sessions_equal_sessions_started(self, busy_team):
        expected = metrics.sessions_started(busy_team, start=_START, end=_END, filters=UsageFilters())

        assert _api_results(busy_team, ["sessions"])["sessions"] == expected

    def test_api_messages_equal_the_messages_block(self, busy_team):
        expected = metrics.messages(busy_team, start=_START, end=_END, filters=UsageFilters())

        assert _api_results(busy_team, ["messages"])["messages"] == expected

    def test_api_participants_equal_active_participants(self, busy_team):
        expected = metrics.active_participants(busy_team, start=_START, end=_END, filters=UsageFilters())

        assert _api_results(busy_team, ["participants"])["participants"] == expected


class TestSurfacesAgreeWhereParametersCoincide:
    """UTC, no tag filter, no archived divergence, the same half-open window."""

    def test_messages_agree(self, busy_team):
        assert _overview(busy_team)["total_messages"] == _api_results(busy_team, ["messages"])["messages"]["total"]

    def test_active_participants_agree(self, busy_team):
        assert _overview(busy_team)["active_participants"] == _api_results(busy_team, ["participants"])["participants"]

    def test_session_metrics_answer_different_questions_and_are_labelled(self, busy_team):
        """`sessions_active` and `sessions_started` are two named metrics, not
        one metric counted twice, so they differ on this fixture - and their
        difference is exactly the sessions that started-but-were-silent and
        were-active-but-started-earlier."""
        active = _overview(busy_team)["total_sessions"]
        started = _api_results(busy_team, ["sessions"])["sessions"]

        assert active == 2  # started_and_active, old_but_active
        assert started == 3  # started_and_active, the never-engaged one, the system-only one


class TestTagFilteredSurfacesAgree:
    """A chat-level tag is the common tagging shape (the session-tag UI writes
    the link against the Chat), and every surface matches it the same way:
    chat-or-message (`chat_tag_exists_pair`). One tagged and one untagged
    conversation pin that the filter narrows every count to the tagged one
    identically - dashboard cards, the bot-performance column, and the API."""

    @pytest.fixture()
    def tagged_team(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        tagged = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(tagged, message_type=ChatMessageType.HUMAN)
        _message(tagged, message_type=ChatMessageType.AI)
        untagged = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.ACTIVE)
        _message(untagged, message_type=ChatMessageType.HUMAN)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=tagged.chat)
        return team, tag

    def test_dashboard_cards_agree_with_each_other_and_the_metrics(self, tagged_team):
        team, tag = tagged_team
        overview = DashboardService(team).get_overview_stats(start_date=_START, end_date=_END, tag_ids=[tag.id])
        filters = UsageFilters(tag_ids=[tag.id])

        assert overview["total_sessions"] == 1
        assert (
            overview["total_messages"] == metrics.messages(team, start=_START, end=_END, filters=filters)["total"] == 2
        )
        assert (
            overview["active_participants"]
            == metrics.active_participants(team, start=_START, end=_END, filters=filters)
            == 1
        )

    def test_bot_performance_messages_agree_with_the_headline(self, tagged_team):
        team, tag = tagged_team
        overview = DashboardService(team).get_overview_stats(start_date=_START, end_date=_END, tag_ids=[tag.id])
        performance = DashboardService(team).get_bot_performance_summary(
            start_date=_START, end_date=_END, tag_ids=[tag.id]
        )

        assert performance["results"][0]["messages"] == overview["total_messages"] == 2


class TestScalarsAndTimeseriesAgree:
    def test_sessions_active_timeseries_sums_to_the_scalar(self, busy_team):
        series = metrics.sessions_active_timeseries(
            busy_team, start=_START, end=_END, granularity="daily", tz=_TZ, filters=UsageFilters()
        )

        assert sum(series.values()) == metrics.sessions_active(
            busy_team, start=_START, end=_END, filters=UsageFilters()
        )

    def test_messages_timeseries_sums_to_the_scalar(self, busy_team):
        series = metrics.messages_timeseries(
            busy_team, start=_START, end=_END, granularity="daily", tz=_TZ, filters=UsageFilters()
        )
        total = metrics.messages(busy_team, start=_START, end=_END, filters=UsageFilters())

        assert sum(block["total"] for block in series.values()) == total["total"]

    def test_sessions_started_plus_in_setup_is_every_session_created_in_the_window(self, busy_team):
        started = metrics.sessions_started(busy_team, start=_START, end=_END, filters=UsageFilters())
        in_setup = metrics.sessions_in_setup(busy_team, start=_START, end=_END, filters=UsageFilters())

        created_in_window = (
            ExperimentSession.objects.filter(team=busy_team, created_at__gte=_START, created_at__lt=_END)
            .exclude(platform=ChannelPlatform.EVALUATIONS)
            .count()
        )
        assert started + in_setup == created_in_window


_AUCKLAND = ZoneInfo("Pacific/Auckland")
_LATE_EVENING_IN_AUCKLAND = datetime(2026, 6, 10, 11, 30, tzinfo=UTC)


def _dashboard_series(points, value_key):
    return {datetime.fromisoformat(point["date"]): point[value_key] for point in points}


def _non_empty(series):
    return {bucket: value for bucket, value in series.items() if value}


@pytest.mark.parametrize("granularity", [Granularity.DAILY, Granularity.WEEKLY, Granularity.MONTHLY])
class TestSurfacesAgreePerBucket:
    """Bucket by bucket, in a zone twelve hours off UTC, so a bucket edge falls between the fixture's
    two message times: `_MID` is already the next day in Auckland, the late-evening message is not.
    The dashboard reads the zone the browser detected; the API takes it as a parameter."""

    @pytest.fixture()
    def split_team(self, busy_team):
        session = ExperimentSession.objects.filter(team=busy_team, status=SessionStatus.ACTIVE).first()
        _message(session, when=_LATE_EVENING_IN_AUCKLAND)
        return busy_team

    def test_messages_agree_on_every_bucket(self, split_team, granularity):
        with timezone.override(_AUCKLAND):
            dashboard = DashboardService(split_team).get_message_volume_data(
                granularity=granularity, start_date=_START, end_date=_END
            )
        api = _api_results(split_team, ["messages"], granularity=granularity, tz=_AUCKLAND)
        shared = metrics.messages_timeseries(
            split_team, start=_START, end=_END, granularity=granularity, tz=_AUCKLAND, filters=UsageFilters()
        )

        expected = {bucket: counts["total"] for bucket, counts in shared.items()}
        assert _dashboard_series(dashboard["totals"], "total_messages") == expected
        assert _non_empty({row["bucket_start"]: row["messages"]["total"] for row in api}) == expected
        assert len(expected) == (2 if granularity == Granularity.DAILY else 1)

    def test_participants_agree_on_every_bucket(self, split_team, granularity):
        with timezone.override(_AUCKLAND):
            dashboard = DashboardService(split_team).get_session_analytics_data(
                granularity=granularity, start_date=_START, end_date=_END
            )
        api = _api_results(split_team, ["participants"], granularity=granularity, tz=_AUCKLAND)
        shared = metrics.active_participants_timeseries(
            split_team, start=_START, end=_END, granularity=granularity, tz=_AUCKLAND, filters=UsageFilters()
        )

        assert _dashboard_series(dashboard["participants"], "active_participants") == shared
        assert _non_empty({row["bucket_start"]: row["participants"] for row in api}) == shared

    def test_dashboard_sessions_read_the_shared_series(self, split_team, granularity):
        with timezone.override(_AUCKLAND):
            dashboard = DashboardService(split_team).get_session_analytics_data(
                granularity=granularity, start_date=_START, end_date=_END
            )
        shared = metrics.sessions_active_timeseries(
            split_team, start=_START, end=_END, granularity=granularity, tz=_AUCKLAND, filters=UsageFilters()
        )

        assert _dashboard_series(dashboard["sessions"], "active_sessions") == shared


class TestHourlyDashboardReadsTheSharedSeries:
    """Hourly is a dashboard-only granularity, so it is checked against `usage_metrics` alone."""

    def test_sessions_and_participants_per_hour(self, busy_team):
        session = ExperimentSession.objects.filter(team=busy_team, status=SessionStatus.ACTIVE).first()
        _message(session, when=_LATE_EVENING_IN_AUCKLAND)
        scope = {"start": _START, "end": _END, "granularity": Granularity.HOURLY, "tz": _TZ}

        dashboard = DashboardService(busy_team).get_session_analytics_data(
            granularity=Granularity.HOURLY, start_date=_START, end_date=_END
        )
        sessions = metrics.sessions_active_timeseries(busy_team, filters=UsageFilters(), **scope)
        participants = metrics.active_participants_timeseries(busy_team, filters=UsageFilters(), **scope)

        assert _dashboard_series(dashboard["sessions"], "active_sessions") == sessions
        assert _dashboard_series(dashboard["participants"], "active_participants") == participants
        assert len(sessions) == 2
