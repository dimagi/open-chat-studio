from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from apps.admin.queries import (
    get_period_totals,
    get_platform_breakdown,
    get_team_activity_summary,
    get_team_stats,
    get_top_experiments,
    get_top_teams,
    get_usage_data,
    get_whatsapp_message_stats,
    get_whatsapp_number_data,
    team_metadata_to_csv,
    top_teams_to_csv,
    usage_to_csv,
)
from apps.admin.views import _compute_growth
from apps.channels.models import ChannelPlatform
from apps.trace.models import Trace, TraceStatus
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationRunFactory,
)
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.traces import TraceFactory


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
        team_a = TeamFactory.create(name="Team A")
        team_b = TeamFactory.create(name="Team B")

        session_a = ExperimentSessionFactory.create(team=team_a, experiment__team=team_a)
        session_b = ExperimentSessionFactory.create(team=team_b, experiment__team=team_b)

        # Team A: 3 messages
        for _ in range(3):
            ChatMessageFactory.create(chat=session_a.chat, message_type="human", content="hi")
        # Team B: 1 message
        ChatMessageFactory.create(chat=session_b.chat, message_type="human", content="hi")

        result = get_top_teams(start, end)
        assert len(result) == 2
        assert result[0]["team"] == "Team A"
        assert result[0]["msg_count"] == 3
        assert result[0]["session_count"] == 1
        assert result[0]["participant_count"] == 1
        assert result[1]["team"] == "Team B"
        assert result[1]["msg_count"] == 1


@pytest.mark.django_db()
class TestTeamMetadataExports:
    def test_top_teams_csv_includes_metadata_columns(self, date_range, settings):
        settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]
        start, end = date_range
        team = TeamFactory.create(name="Team A", metadata={"team_owner": "Jane Doe"})
        session = ExperimentSessionFactory.create(team=team, experiment__team=team)
        ChatMessageFactory.create(chat=session.chat, message_type="human", content="hi")

        csv_output = top_teams_to_csv(start, end)
        lines = csv_output.strip().splitlines()
        assert lines[0] == "Team,Messages,Sessions,Participants,Team Owner"
        assert lines[1].startswith("Team A,")
        assert lines[1].endswith(",Jane Doe")

    def test_usage_csv_includes_metadata_columns(self, date_range, settings):
        settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]
        start, end = date_range
        team = TeamFactory.create(name="Team A", metadata={"team_owner": "Jane Doe"})
        session = ExperimentSessionFactory.create(team=team, experiment__team=team)
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            status=TraceStatus.SUCCESS,
            n_total_tokens=100,
        )

        csv_output = usage_to_csv(start, end)
        lines = csv_output.strip().splitlines()
        assert lines[0] == "Team,Run Count,Total Tokens,Team Owner"
        assert lines[1] == "Team A,1,100,Jane Doe"

    def test_team_metadata_csv_lists_all_teams(self, settings):
        settings.TEAM_METADATA_FIELDS = [{"key": "team_owner", "label": "Team Owner"}]
        TeamFactory.create(name="Team A", slug="team-a", metadata={"team_owner": "Jane Doe"})
        TeamFactory.create(name="Team B", slug="team-b", metadata={})

        csv_output = team_metadata_to_csv()
        lines = csv_output.strip().splitlines()
        assert lines[0] == "Team,Slug,Team Owner"
        assert "Team A,team-a,Jane Doe" in lines
        assert "Team B,team-b," in lines


@pytest.mark.django_db()
class TestGetTeamStats:
    def test_resource_counts_scoped_to_team(self):
        # No session/channel factories here: those spawn extra experiments, which would
        # make the chatbot count non-deterministic.
        team = TeamFactory.create()
        other = TeamFactory.create()

        ExperimentFactory.create(team=team)
        ExperimentFactory.create(team=other)  # excluded: different team
        CollectionFactory.create(team=team)
        MembershipFactory.create(team=team)
        EvaluationConfigFactory.create(team=team)
        EvaluationRunFactory.create(team=team)
        EvaluationDatasetFactory.create(team=team)

        stats = get_team_stats(team)
        assert stats["chatbots"] == 1
        assert stats["collections"] == 1
        assert stats["members"] == 1
        assert stats["evaluation_configs"] == 1
        assert stats["evaluation_runs"] == 1
        assert stats["evaluation_datasets"] == 1

    def test_activity_counts(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team, experiment__team=team)
        ChatMessageFactory.create(chat=session.chat, message_type="human", content="hi")
        ChatMessageFactory.create(chat=session.chat, message_type="ai", content="hello")
        ParticipantFactory.create(team=team)

        stats = get_team_stats(team)
        assert stats["messages"] == 2
        assert stats["sessions"] == 1
        # The session factory creates a participant; ParticipantFactory adds one more.
        assert stats["participants"] == 2

    def test_excludes_evaluations_platform(self):
        team = TeamFactory.create()
        eval_channel = ExperimentChannelFactory.create(team=team, platform=ChannelPlatform.EVALUATIONS)
        eval_session = ExperimentSessionFactory.create(
            team=team,
            experiment=eval_channel.experiment,
            experiment_channel=eval_channel,
            platform=ChannelPlatform.EVALUATIONS,
        )
        ChatMessageFactory.create(chat=eval_session.chat, message_type="human", content="hi")
        ParticipantFactory.create(team=team, platform=ChannelPlatform.EVALUATIONS)

        stats = get_team_stats(team)
        assert stats["messages"] == 0
        assert stats["sessions"] == 0
        # The evaluations participant is excluded; only the session's default-platform one counts.
        assert stats["participants"] == 1


@pytest.mark.django_db()
class TestGetPlatformBreakdown:
    def test_excludes_evaluations(self, date_range):
        start, end = date_range
        team = TeamFactory.create()

        # Web session with a message
        web_channel = ExperimentChannelFactory.create(team=team, platform=ChannelPlatform.WEB)
        web_session = ExperimentSessionFactory.create(
            team=team,
            experiment=web_channel.experiment,
            experiment_channel=web_channel,
            platform=ChannelPlatform.WEB,
        )
        ChatMessageFactory.create(chat=web_session.chat, message_type="human", content="hi")

        # Telegram session with a message
        tg_channel = ExperimentChannelFactory.create(team=team, platform=ChannelPlatform.TELEGRAM)
        tg_session = ExperimentSessionFactory.create(
            team=team,
            experiment=tg_channel.experiment,
            experiment_channel=tg_channel,
            platform=ChannelPlatform.TELEGRAM,
        )
        ChatMessageFactory.create(chat=tg_session.chat, message_type="human", content="hi")

        # Evaluations session (should be excluded)
        eval_channel = ExperimentChannelFactory.create(team=team, platform=ChannelPlatform.EVALUATIONS)
        ExperimentSessionFactory.create(
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
        team_active1 = TeamFactory.create(name="Active 1")
        team_active2 = TeamFactory.create(name="Active 2")
        TeamFactory.create(name="Inactive 1")

        session1 = ExperimentSessionFactory.create(team=team_active1, experiment__team=team_active1)
        session2 = ExperimentSessionFactory.create(team=team_active2, experiment__team=team_active2)
        ChatMessageFactory.create(chat=session1.chat, message_type="human", content="hi")
        ChatMessageFactory.create(chat=session2.chat, message_type="human", content="hi")

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
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team, experiment__team=team)
        ChatMessageFactory.create(chat=session.chat, message_type="human", content="hi")
        ChatMessageFactory.create(chat=session.chat, message_type="ai", content="hello")
        ParticipantFactory.create(team=team)

        result = get_period_totals(start, end)
        assert result["messages"] >= 2
        assert result["participants"] >= 1
        assert result["sessions"] >= 1


@pytest.mark.django_db()
class TestGetTopExperiments:
    def test_limit_and_ordering(self, date_range):
        start, end = date_range
        team = TeamFactory.create()

        # Create 3 experiments with different message counts
        sessions = []
        for i in range(3):
            session = ExperimentSessionFactory.create(team=team, experiment__team=team)
            sessions.append(session)
            for _ in range(3 - i):
                ChatMessageFactory.create(chat=session.chat, message_type="human", content="hi")

        result = get_top_experiments(start, end, limit=2)
        assert len(result) == 2
        assert result[0]["msg_count"] >= result[1]["msg_count"]


@pytest.mark.django_db()
class TestGetWhatsappMessageStats:
    def test_pivot_marks_channel_active_when_any_channel_active(self, date_range):
        # When a WhatsApp channel for a number is deleted and recreated, the query returns
        # separate rows per deleted status. The pivoted result should report the channel
        # as active if any underlying channel is active.
        start, end = date_range
        team = TeamFactory.create(name="WA Team")
        number = "+15550001111"

        deleted_channel = ExperimentChannelFactory.create(
            team=team,
            platform=ChannelPlatform.WHATSAPP,
            extra_data={"number": number},
        )
        active_channel = ExperimentChannelFactory.create(
            team=team,
            experiment=deleted_channel.experiment,
            platform=ChannelPlatform.WHATSAPP,
            extra_data={"number": number},
        )
        deleted_channel.deleted = True
        deleted_channel.save()

        deleted_session = ExperimentSessionFactory.create(
            team=team,
            experiment=deleted_channel.experiment,
            experiment_channel=deleted_channel,
            platform=ChannelPlatform.WHATSAPP,
        )
        active_session = ExperimentSessionFactory.create(
            team=team,
            experiment=active_channel.experiment,
            experiment_channel=active_channel,
            platform=ChannelPlatform.WHATSAPP,
        )
        ChatMessageFactory.create(chat=deleted_session.chat, message_type="human", content="old")
        ChatMessageFactory.create(chat=active_session.chat, message_type="human", content="new")
        ChatMessageFactory.create(chat=active_session.chat, message_type="ai", content="reply")

        results = get_whatsapp_message_stats(start, end)
        rows = [r for r in results if r["number"] == number]
        assert len(rows) == 1
        row = rows[0]
        assert row["channel_active"] is True
        assert row["human_count"] == 2
        assert row["ai_count"] == 1

    def test_pivot_marks_channel_inactive_when_all_channels_deleted(self, date_range):
        start, end = date_range
        team = TeamFactory.create(name="WA Team 2")
        number = "+15550002222"

        channel = ExperimentChannelFactory.create(
            team=team,
            platform=ChannelPlatform.WHATSAPP,
            extra_data={"number": number},
        )
        session = ExperimentSessionFactory.create(
            team=team,
            experiment=channel.experiment,
            experiment_channel=channel,
            platform=ChannelPlatform.WHATSAPP,
        )
        ChatMessageFactory.create(chat=session.chat, message_type="human", content="hi")
        channel.deleted = True
        channel.save()

        results = get_whatsapp_message_stats(start, end)
        rows = [r for r in results if r["number"] == number]
        assert len(rows) == 1
        assert rows[0]["channel_active"] is False


@pytest.mark.django_db()
class TestGetWhatsappNumberData:
    def test_includes_deleted_channels_with_correct_active_flag(self):
        # The default ExperimentChannel manager filters out deleted rows, so the export
        # must bypass it to show both active and deleted channels accurately.
        team = TeamFactory.create(name="WA Numbers")

        active_channel = ExperimentChannelFactory.create(
            team=team,
            platform=ChannelPlatform.WHATSAPP,
            extra_data={"number": "+15550003333"},
        )
        deleted_channel = ExperimentChannelFactory.create(
            team=team,
            platform=ChannelPlatform.WHATSAPP,
            extra_data={"number": "+15550004444"},
        )
        deleted_channel.deleted = True
        deleted_channel.save()

        rows_by_number = {row[4]: row for row in get_whatsapp_number_data()}
        assert "+15550003333" in rows_by_number
        assert "+15550004444" in rows_by_number
        assert rows_by_number["+15550003333"][5] is True  # active channel
        assert rows_by_number["+15550004444"][5] is False  # deleted channel
        assert rows_by_number["+15550003333"][1] == active_channel.experiment.name
        assert rows_by_number["+15550004444"][1] == deleted_channel.experiment.name


@pytest.mark.django_db()
class TestGetUsageData:
    def _make_trace(self, *, session, status=TraceStatus.SUCCESS, tokens=0):
        return TraceFactory.create(
            team=session.team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            status=status,
            n_total_tokens=tokens,
        )

    def test_sums_tokens_and_counts_runs_per_team(self, date_range):
        start, end = date_range
        team_a = TeamFactory.create(name="Team A")
        team_b = TeamFactory.create(name="Team B")
        session_a = ExperimentSessionFactory.create(team=team_a, experiment__team=team_a)
        session_b = ExperimentSessionFactory.create(team=team_b, experiment__team=team_b)

        self._make_trace(session=session_a, tokens=100)
        self._make_trace(session=session_a, tokens=50)
        self._make_trace(session=session_b, tokens=200)

        rows = {team: (run_count, tokens) for team, run_count, tokens, _ in get_usage_data(start, end)}
        assert rows["Team A"] == (2, 150)
        assert rows["Team B"] == (1, 200)

    def test_orders_by_run_count_descending(self, date_range):
        start, end = date_range
        team_small = TeamFactory.create(name="Small")
        team_big = TeamFactory.create(name="Big")
        small_session = ExperimentSessionFactory.create(team=team_small, experiment__team=team_small)
        big_session = ExperimentSessionFactory.create(team=team_big, experiment__team=team_big)

        self._make_trace(session=small_session, tokens=10)
        for _ in range(3):
            self._make_trace(session=big_session, tokens=10)

        result = list(get_usage_data(start, end))
        assert [row[0] for row in result] == ["Big", "Small"]

    def test_excludes_evaluations_platform(self, date_range):
        start, end = date_range
        team = TeamFactory.create(name="T")
        web_channel = ExperimentChannelFactory.create(team=team, platform=ChannelPlatform.WEB)
        eval_channel = ExperimentChannelFactory.create(team=team, platform=ChannelPlatform.EVALUATIONS)
        web_session = ExperimentSessionFactory.create(
            team=team,
            experiment=web_channel.experiment,
            experiment_channel=web_channel,
            platform=ChannelPlatform.WEB,
        )
        eval_session = ExperimentSessionFactory.create(
            team=team,
            experiment=eval_channel.experiment,
            experiment_channel=eval_channel,
            platform=ChannelPlatform.EVALUATIONS,
        )
        self._make_trace(session=web_session, tokens=100)
        self._make_trace(session=eval_session, tokens=99999)

        rows = list(get_usage_data(start, end))
        assert rows == [("T", 1, 100, {})]

    def test_excludes_pending_traces_but_includes_errors(self, date_range):
        start, end = date_range
        team = TeamFactory.create(name="T")
        session = ExperimentSessionFactory.create(team=team, experiment__team=team)
        self._make_trace(session=session, status=TraceStatus.SUCCESS, tokens=10)
        self._make_trace(session=session, status=TraceStatus.ERROR, tokens=5)
        self._make_trace(session=session, status=TraceStatus.PENDING, tokens=99999)

        rows = list(get_usage_data(start, end))
        assert rows == [("T", 2, 15, {})]

    def test_handles_null_token_counts(self, date_range):
        start, end = date_range
        team = TeamFactory.create(name="T")
        session = ExperimentSessionFactory.create(team=team, experiment__team=team)
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            status=TraceStatus.SUCCESS,
            n_total_tokens=None,
        )

        rows = list(get_usage_data(start, end))
        assert rows == [("T", 1, 0, {})]

    def test_does_not_merge_teams_sharing_a_display_name(self, date_range):
        # Team.name has no uniqueness constraint, so grouping by name alone would
        # incorrectly combine separate teams. Each team must aggregate independently.
        start, end = date_range
        team_one = TeamFactory.create(name="Duplicate")
        team_two = TeamFactory.create(name="Duplicate")
        session_one = ExperimentSessionFactory.create(team=team_one, experiment__team=team_one)
        session_two = ExperimentSessionFactory.create(team=team_two, experiment__team=team_two)

        self._make_trace(session=session_one, tokens=10)
        self._make_trace(session=session_two, tokens=20)
        self._make_trace(session=session_two, tokens=30)

        rows = list(get_usage_data(start, end))
        assert len(rows) == 2
        token_totals = sorted(row[2] for row in rows)
        run_counts = sorted(row[1] for row in rows)
        assert token_totals == [10, 50]
        assert run_counts == [1, 2]
        assert all(row[0] == "Duplicate" for row in rows)

    def test_filters_by_date_range(self, date_range):
        start, end = date_range
        team = TeamFactory.create(name="T")
        session = ExperimentSessionFactory.create(team=team, experiment__team=team)
        in_window = self._make_trace(session=session, tokens=10)
        out_of_window = self._make_trace(session=session, tokens=99999)
        # Trace.timestamp uses auto_now_add; bypass via update()
        Trace.objects.filter(id=out_of_window.id).update(timestamp=start - timedelta(seconds=1))
        Trace.objects.filter(id=in_window.id).update(timestamp=start + timedelta(seconds=1))

        rows = list(get_usage_data(start, end))
        assert rows == [("T", 1, 10, {})]


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
