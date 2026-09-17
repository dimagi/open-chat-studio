from datetime import date
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, Participant, SessionStatus

from ..engagement_service import (
    TRAILING_MONTHS,
    EngagementDashboardService,
    _add_months,
    _cache_key,
    trailing_window,
    weekly_activity_by_month,
)
from ..models import DashboardCache


def _create_session(experiment, participant, team):
    return ExperimentSession.objects.create(
        experiment=experiment, participant=participant, team=team, status=SessionStatus.ACTIVE
    )


def _create_message(session, created_at, message_type=ChatMessageType.HUMAN):
    message = ChatMessage.objects.create(chat=session.chat, message_type=message_type)
    message.created_at = created_at
    message.save()
    return message


class TestAddMonths:
    def test_wraps_year_boundary_backwards(self):
        assert _add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)

    def test_wraps_year_boundary_forwards(self):
        assert _add_months(date(2025, 12, 1), 1) == date(2026, 1, 1)


class TestTrailingWindow:
    def test_returns_seven_months_oldest_first_ending_on_current_month(self):
        now = timezone.datetime(2026, 3, 15, 12, 0, tzinfo=ZoneInfo("UTC"))

        start, end, months = trailing_window(now)

        assert len(months) == TRAILING_MONTHS + 1
        assert months == sorted(months)
        assert months[-1] == date(2026, 3, 1)
        assert months[0] == date(2025, 9, 1)
        assert start == timezone.datetime(2025, 9, 1, tzinfo=ZoneInfo("UTC"))
        assert end == now


@pytest.mark.django_db()
class TestWeeklyActivityByMonth:
    def test_buckets_distinct_weeks_per_participant_per_month(self, team, experiment, participant):
        other_participant = Participant.objects.create(team=team, platform="web", identifier="other@example.com")
        session = _create_session(experiment, participant, team)
        other_session = _create_session(experiment, other_participant, team)
        now = timezone.datetime(2026, 3, 20, 12, 0, tzinfo=ZoneInfo("UTC"))

        _create_message(session, timezone.datetime(2026, 3, 3, 9, 0, tzinfo=ZoneInfo("UTC")))
        _create_message(session, timezone.datetime(2026, 3, 10, 9, 0, tzinfo=ZoneInfo("UTC")))
        _create_message(other_session, timezone.datetime(2026, 3, 3, 9, 0, tzinfo=ZoneInfo("UTC")))

        activity = weekly_activity_by_month(team, filters={}, now=now)

        assert activity[date(2026, 3, 1)] == {participant.id: 2, other_participant.id: 1}

    def test_week_spanning_month_boundary_assigned_to_the_weeks_starting_month(self, team, experiment, participant):
        session = _create_session(experiment, participant, team)
        now = timezone.datetime(2026, 3, 5, 12, 0, tzinfo=ZoneInfo("UTC"))
        _create_message(session, timezone.datetime(2026, 3, 1, 9, 0, tzinfo=ZoneInfo("UTC")))

        activity = weekly_activity_by_month(team, filters={}, now=now)

        assert activity[date(2026, 2, 1)] == {participant.id: 1}
        assert participant.id not in activity[date(2026, 3, 1)]

    def test_month_with_no_activity_is_present_but_empty(self, team, experiment, participant):
        now = timezone.datetime(2026, 3, 5, 12, 0, tzinfo=ZoneInfo("UTC"))

        activity = weekly_activity_by_month(team, filters={}, now=now)

        assert activity[date(2026, 1, 1)] == {}

    def test_only_human_messages_count_as_activity(self, team, experiment, participant):
        session = _create_session(experiment, participant, team)
        now = timezone.datetime(2026, 3, 5, 12, 0, tzinfo=ZoneInfo("UTC"))
        _create_message(session, timezone.datetime(2026, 3, 3, 9, 0, tzinfo=ZoneInfo("UTC")), ChatMessageType.AI)

        activity = weekly_activity_by_month(team, filters={}, now=now)

        assert activity[date(2026, 3, 1)] == {}


@pytest.mark.django_db()
class TestGetEngagementSummaryData:
    def test_returns_seven_months_with_mau_and_core_users_rate_for_current_month(self, team, experiment, participant):
        other_participant = Participant.objects.create(team=team, platform="web", identifier="other@example.com")
        session = _create_session(experiment, participant, team)
        other_session = _create_session(experiment, other_participant, team)

        now = timezone.now()
        month_start = date(now.year, now.month, 1)
        week_one = timezone.datetime.combine(
            month_start, timezone.datetime.min.time(), tzinfo=ZoneInfo("UTC")
        ) + timezone.timedelta(days=7, hours=9)
        week_two = week_one + timezone.timedelta(days=7)

        _create_message(session, week_one)
        _create_message(session, week_two)
        _create_message(other_session, week_one)

        data = EngagementDashboardService(team).get_engagement_summary_data()

        assert len(data) == TRAILING_MONTHS + 1
        current = data[-1]
        assert current["month"] == month_start.isoformat()
        assert current["mau"] == 2
        assert current["core_users_rate"] == pytest.approx(50.0)
        assert current["in_progress"] is True
        assert all(month["in_progress"] is False for month in data[:-1])

    def test_month_with_no_active_participants_has_zero_core_users_rate(self, team):
        data = EngagementDashboardService(team).get_engagement_summary_data()

        oldest = data[0]
        assert oldest["mau"] == 0
        assert oldest["core_users_rate"] == 0

    def test_result_is_cached(self, team):
        service = EngagementDashboardService(team)
        first = service.get_engagement_summary_data()

        cached = DashboardCache.get_cached_data(team, f"engagement_summary_{_cache_key({})}")

        assert cached == first
