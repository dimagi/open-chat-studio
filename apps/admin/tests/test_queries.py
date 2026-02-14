from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from apps.admin.queries import (
    get_period_totals,
    get_platform_breakdown,
    get_team_activity_summary,
    get_top_experiments,
    get_top_teams,
)
from apps.admin.views import _compute_growth
from apps.channels.models import ChannelPlatform
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def date_range():
    today = timezone.now().date()
    tz = timezone.get_current_timezone()
    start = datetime.combine(today - timedelta(days=30), time.min, tzinfo=tz)
    end = datetime.combine(today, time.max, tzinfo=tz)
    return start, end


@pytest.mark.django_db()
class TestGetTopTeams:
    def test_ordering_and_counts(self, date_range):
        start, end = date_range
        team_a = TeamFactory(name="Team A")
        team_b = TeamFactory(name="Team B")

        session_a = ExperimentSessionFactory(team=team_a, experiment__team=team_a)
        session_b = ExperimentSessionFactory(team=team_b, experiment__team=team_b)

        # Team A: 3 messages
        for _ in range(3):
            ChatMessageFactory(chat=session_a.chat, message_type="human", content="hi")
        # Team B: 1 message
        ChatMessageFactory(chat=session_b.chat, message_type="human", content="hi")

        result = get_top_teams(start, end)
        assert len(result) == 2
        assert result[0]["team"] == "Team A"
        assert result[0]["msg_count"] == 3
        assert result[0]["session_count"] == 1
        assert result[0]["participant_count"] == 1
        assert result[1]["team"] == "Team B"
        assert result[1]["msg_count"] == 1


@pytest.mark.django_db()
class TestGetPlatformBreakdown:
    def test_excludes_evaluations(self, date_range):
        start, end = date_range
        team = TeamFactory()

        # Web session with a message
        web_channel = ExperimentChannelFactory(team=team, platform=ChannelPlatform.WEB)
        web_session = ExperimentSessionFactory(
            team=team,
            experiment=web_channel.experiment,
            experiment_channel=web_channel,
            platform=ChannelPlatform.WEB,
        )
        ChatMessageFactory(chat=web_session.chat, message_type="human", content="hi")

        # Telegram session with a message
        tg_channel = ExperimentChannelFactory(team=team, platform=ChannelPlatform.TELEGRAM)
        tg_session = ExperimentSessionFactory(
            team=team,
            experiment=tg_channel.experiment,
            experiment_channel=tg_channel,
            platform=ChannelPlatform.TELEGRAM,
        )
        ChatMessageFactory(chat=tg_session.chat, message_type="human", content="hi")

        # Evaluations session (should be excluded)
        eval_channel = ExperimentChannelFactory(team=team, platform=ChannelPlatform.EVALUATIONS)
        ExperimentSessionFactory(
            team=team,
            experiment=eval_channel.experiment,
            experiment_channel=eval_channel,
            platform=ChannelPlatform.EVALUATIONS,
        )

        result = get_platform_breakdown(start, end)
        platforms = [r["platform"] for r in result]
        assert "Evaluations" not in platforms
        assert len(result) == 2


@pytest.mark.django_db()
class TestGetTeamActivitySummary:
    def test_active_and_inactive(self, date_range):
        start, end = date_range
        team_active1 = TeamFactory(name="Active 1")
        team_active2 = TeamFactory(name="Active 2")
        TeamFactory(name="Inactive 1")

        session1 = ExperimentSessionFactory(team=team_active1, experiment__team=team_active1)
        session2 = ExperimentSessionFactory(team=team_active2, experiment__team=team_active2)
        ChatMessageFactory(chat=session1.chat, message_type="human", content="hi")
        ChatMessageFactory(chat=session2.chat, message_type="human", content="hi")

        result = get_team_activity_summary(start, end)
        assert result["active_count"] == 2
        assert result["total_count"] >= 3
        inactive_names = [t["name"] for t in result["inactive_teams"]]
        assert "Inactive 1" in inactive_names
        assert "Active 1" not in inactive_names
        assert "Active 2" not in inactive_names


@pytest.mark.django_db()
class TestGetPeriodTotals:
    def test_counts(self, date_range):
        start, end = date_range
        team = TeamFactory()
        session = ExperimentSessionFactory(team=team, experiment__team=team)
        ChatMessageFactory(chat=session.chat, message_type="human", content="hi")
        ChatMessageFactory(chat=session.chat, message_type="ai", content="hello")
        ParticipantFactory(team=team)

        result = get_period_totals(start, end)
        assert result["messages"] >= 2
        assert result["participants"] >= 1
        assert result["sessions"] >= 1


@pytest.mark.django_db()
class TestGetTopExperiments:
    def test_limit_and_ordering(self, date_range):
        start, end = date_range
        team = TeamFactory()

        # Create 3 experiments with different message counts
        sessions = []
        for i in range(3):
            session = ExperimentSessionFactory(team=team, experiment__team=team)
            sessions.append(session)
            for _ in range(3 - i):
                ChatMessageFactory(chat=session.chat, message_type="human", content="hi")

        result = get_top_experiments(start, end, limit=2)
        assert len(result) == 2
        assert result[0]["msg_count"] >= result[1]["msg_count"]


class TestComputeGrowth:
    def test_positive_growth(self):
        current = {"messages": 200, "participants": 50, "sessions": 100}
        previous = {"messages": 100, "participants": 25, "sessions": 50}
        result = _compute_growth(current, previous)
        assert result[0]["pct_change"] == 100.0
        assert result[1]["pct_change"] == 100.0

    def test_negative_growth(self):
        current = {"messages": 50, "participants": 10, "sessions": 25}
        previous = {"messages": 100, "participants": 20, "sessions": 50}
        result = _compute_growth(current, previous)
        assert result[0]["pct_change"] == -50.0

    def test_zero_previous(self):
        current = {"messages": 10, "participants": 5, "sessions": 3}
        previous = {"messages": 0, "participants": 0, "sessions": 0}
        result = _compute_growth(current, previous)
        assert result[0]["pct_change"] == 100.0

    def test_both_zero(self):
        current = {"messages": 0, "participants": 0, "sessions": 0}
        previous = {"messages": 0, "participants": 0, "sessions": 0}
        result = _compute_growth(current, previous)
        assert result[0]["pct_change"] == 0.0
