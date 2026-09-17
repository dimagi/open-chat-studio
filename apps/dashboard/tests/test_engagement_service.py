from datetime import date
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, Participant, SessionStatus

from ..engagement_service import TRAILING_MONTHS, _add_months, trailing_window, weekly_activity_by_month


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
