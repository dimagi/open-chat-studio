from datetime import timedelta
from unittest.mock import ANY

import pytest
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession, SessionStatus
from apps.utils.factories.annotations import CustomTaggedItemFactory, TagFactory
from apps.utils.factories.team import TeamFactory

from ..models import DashboardCache
from ..services import DashboardService


@pytest.mark.django_db()
class TestDashboardService:
    """Test dashboard service functionality"""

    def test_service_initialization(self, team):
        """Test service initialization"""
        service = DashboardService(team)
        assert service.team == team

    def test_get_filtered_queryset_base(self, team, experiment, participant, experiment_session, chat):
        """Test basic queryset filtering"""
        service = DashboardService(team)

        # Test without filters
        querysets = service.get_filtered_queryset_base()

        assert "experiments" in querysets
        assert "sessions" in querysets
        assert "messages" in querysets
        assert "participants" in querysets
        assert "start_date" in querysets
        assert "end_date" in querysets

        # Verify team filtering
        experiments = querysets["experiments"]
        assert all(exp.team == team for exp in experiments)

    def test_date_range_filtering(self, team, experiment, participant):
        """Test date range filtering"""
        service = DashboardService(team)

        # Create sessions on different dates
        old_date = timezone.now() - timedelta(days=60)
        recent_date = timezone.now() - timedelta(days=5)

        old_session = _create_session(experiment, participant, team, old_date)
        recent_session = _create_session(experiment, participant, team, recent_date)

        # Filter for last 30 days
        start_date = timezone.now() - timedelta(days=30)
        end_date = timezone.now()

        querysets = service.get_filtered_queryset_base(start_date=start_date, end_date=end_date)

        sessions = list(querysets["sessions"])
        session_ids = [s.id for s in sessions]

        # Should include recent session but not old session
        assert recent_session.id in session_ids
        assert old_session.id not in session_ids

    def test_experiment_filtering(self, team, experiment, experiment_team, participant):
        """Test experiment filtering"""
        service = DashboardService(team)

        # Create another experiment
        other_experiment = Experiment.objects.create(name="Other Experiment", team=team, owner=experiment.owner)

        # Create sessions for both experiments
        session1 = _create_session(experiment, participant, team, timezone.now())

        session2 = _create_session(other_experiment, participant, team, timezone.now())

        # Filter by specific experiment
        querysets = service.get_filtered_queryset_base(experiment_ids=[experiment.id])

        sessions = list(querysets["sessions"])
        session_ids = [s.id for s in sessions]

        # Should include only session from filtered experiment
        assert session1.id in session_ids
        assert session2.id not in session_ids

    def test_get_overview_stats(self, team, experiment, participant, experiment_session, chat):
        """Test overview statistics generation"""
        service = DashboardService(team)

        # Create test messages
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Human message")
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.AI, content="AI message")

        stats = service.get_overview_stats()

        # Check required fields
        assert "total_experiments" in stats
        assert "total_participants" in stats
        assert "total_sessions" in stats
        assert "total_messages" in stats
        assert "completion_rate" in stats

        # Verify counts
        assert stats["total_experiments"] >= 1
        assert stats["total_participants"] >= 1
        assert stats["total_sessions"] >= 1
        assert stats["total_messages"] >= 2

    def test_get_session_analytics_data(self, team, experiment, participant, experiment_session, chat):
        """Test session analytics data generation"""

        message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Human message")
        message.created_at = timezone.now() - timedelta(days=15)
        message.save()

        assert message.created_at != experiment_session.created_at

        service = DashboardService(team)

        data = service.get_session_analytics_data(granularity="daily")
        assert data == {
            "sessions": [{"date": str(message.created_at.date()), "active_sessions": 1}],
            "participants": [{"date": str(message.created_at.date()), "active_participants": 1}],
        }

    def test_get_message_volume_data(self, team, experiment, participant, experiment_session, chat):
        """Test message volume data generation"""
        service = DashboardService(team)

        # Create messages of different types
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Human message")
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.AI, content="AI message")

        data = service.get_message_volume_data(granularity="daily")

        assert isinstance(data, dict)
        assert "human_messages" in data
        assert "ai_messages" in data
        assert "totals" in data

        for key in ["human_messages", "ai_messages", "totals"]:
            assert isinstance(data[key], list)

    def test_get_message_volume_data_total_excludes_system_messages(
        self, team, experiment, participant, experiment_session, chat
    ):
        """The chart's per-period total is human + ai, not every message type
        (ADR-0051): a SYSTEM message must not inflate it."""
        service = DashboardService(team)

        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Human message")
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.AI, content="AI message")
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.SYSTEM, content="System message")

        data = service.get_message_volume_data(granularity="daily")

        assert len(data["totals"]) == 1
        period = data["totals"][0]
        assert period["human_messages"] == 1
        assert period["ai_messages"] == 1
        assert period["total_messages"] == 2
        assert period["total_messages"] == period["human_messages"] + period["ai_messages"]

    def test_get_bot_performance_summary(self, team, experiment, participant, experiment_session, chat):
        """Test bot performance summary generation"""
        service = DashboardService(team)

        # Create some messages and end the session
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Human message")

        experiment_session.ended_at = timezone.now()
        experiment_session.save()

        data = service.get_bot_performance_summary()

        assert isinstance(data["results"], list)
        if data:  # If there's data
            item = data["results"][0]
            expected_fields = [
                "experiment_id",
                "experiment_name",
                "participants",
                "sessions",
                "messages",
                "completion_rate",
            ]
            for field in expected_fields:
                assert field in item

    @pytest.mark.django_db()
    def test_bot_performance_messages_obey_the_window_and_message_types(self, team, experiment, participant):
        """The per-chatbot Messages column counts the same rows the headline
        total does: conversation turns inside the window (ADR-0051)."""
        now = timezone.now()
        session = ExperimentSession.objects.create(
            experiment=experiment, participant=participant, team=team, status="active"
        )
        chat = session.chat
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="in window")
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.AI, content="in window")
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.SYSTEM, content="not a turn")
        ChatMessage.objects.create(
            chat=chat,
            message_type=ChatMessageType.HUMAN,
            content="before the window",
            created_at=now - timedelta(days=90),
        )

        data = DashboardService(team).get_bot_performance_summary(
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1)
        )

        assert data["results"][0]["messages"] == 2

    def test_get_channel_breakdown_data(self, team, experiment, participant, experiment_channel):
        """Test channel breakdown data generation"""
        service = DashboardService(team)

        # Create session with channel
        session = ExperimentSession.objects.create(
            experiment=experiment, participant=participant, team=team, experiment_channel=experiment_channel
        )

        chat = Chat.objects.create(team=team, name="Test Chat")
        session.chat = chat
        session.save()

        # Create message
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test message")

        data = service.get_channel_breakdown_data()

        assert isinstance(data, dict)
        assert "platforms" in data
        assert "totals" in data
        assert isinstance(data["platforms"], list)

        if data["platforms"]:
            channel_data = data["platforms"][0]
            expected_fields = ["platform", "sessions"]
            for field in expected_fields:
                assert field in channel_data

    def test_get_user_engagement_data(self, team, experiment, participant, experiment_session, chat):
        """Test user engagement data generation"""
        service = DashboardService(team)

        # Create messages to make participant active
        for i in range(3):
            ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content=f"Message {i}")

        data = service.get_user_engagement_data(limit=5)

        assert isinstance(data, dict)
        assert data["most_active_participants"] == [
            {
                "participant_id": participant.id,
                "participant_name": participant.name,
                "participant_url": ANY,
                "total_messages": 3,
                "total_sessions": 1,
                "last_activity": ANY,
                "cost": 0.0,
            }
        ]

        assert isinstance(data["most_active_participants"], list)
        assert isinstance(data["session_length_distribution"], list)

    def test_granularity_options(self, team, experiment, participant, experiment_session, chat):
        """Test different granularity options"""
        service = DashboardService(team)

        # Create test message
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test message")

        granularities = ["hourly", "daily", "weekly", "monthly"]

        for granularity in granularities:
            data = service.get_session_analytics_data(granularity=granularity)
            assert isinstance(data, dict)

            # Test that the function doesn't crash with different granularities
            # The exact data will depend on when the test is run

    def test_histogram_creation(self, team):
        """Test histogram creation utility"""
        service = DashboardService(team)

        # Test with sample data
        test_data = [1.5, 2.3, 3.1, 4.7, 5.2, 6.8, 7.1, 8.9, 9.2, 10.5]
        histogram = service._create_histogram(test_data, bins=5)

        assert len(histogram) == 5
        assert all("bin_start" in bin_data for bin_data in histogram)
        assert all("bin_end" in bin_data for bin_data in histogram)
        assert all("count" in bin_data for bin_data in histogram)
        assert all("label" in bin_data for bin_data in histogram)

        # Verify total count matches input data
        total_count = sum(bin_data["count"] for bin_data in histogram)
        assert total_count == len(test_data)

    def test_empty_data_handling(self, team):
        """Test service behavior with empty data"""
        service = DashboardService(team)

        # Test various methods with no data
        stats = service.get_overview_stats()
        assert all(value >= 0 for value in stats.values() if isinstance(value, int | float))

        session_data = service.get_session_analytics_data()
        assert isinstance(session_data, dict)

        performance_data = service.get_bot_performance_summary()
        assert isinstance(performance_data["results"], list)

    def test_caching_behavior(self, team, experiment, participant, experiment_session, chat):
        """Test caching behavior in service methods"""

        service = DashboardService(team)

        # Create test data
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test message")

        # First call - should create cache
        data1 = service.get_overview_stats()

        # Check that cache was created
        cache_entries = DashboardCache.objects.filter(team=team)
        assert cache_entries.exists()

        # Second call - should use cache
        data2 = service.get_overview_stats()

        # Data should be identical
        assert data1 == data2


@pytest.mark.django_db()
class TestGetTagAnalyticsDataTeamScoping:
    """`get_tag_analytics_data` reads `CustomTaggedItem` rows directly (not
    via a team-scoped queryset like the other dashboard reads), so it needs
    its own team-scoping pin. Both the link's own `team_id` and its tag's
    `team_id` must match the reading team, or a foreign team's tag - and its
    user-authored name - leaks into this team's breakdown.

    Each test uses its own `team` fixture instance (function-scoped, so a
    fresh team and a fresh `DashboardCache` key every time) to guarantee a
    cached value from another test can never serve a result here.
    """

    @pytest.mark.parametrize(
        "failing_conjunct",
        [
            pytest.param("link-team", id="link-team-conjunct"),
            pytest.param("tag-team", id="tag-team-conjunct"),
        ],
    )
    def test_link_failing_one_scoping_conjunct_is_excluded(self, team, chat, failing_conjunct):
        """The read scopes tag links with two conjuncts, the link row's
        `team_id` and its tag's `team_id`. Each case builds a link where
        exactly one conjunct fails while the other holds, so deleting either
        predicate turns exactly its named case red. Message links only -
        this read has no chat leg. Positive control:
        `test_own_teams_link_and_tag_are_counted`."""
        message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="hi")
        foreign_team = TeamFactory.create()
        link_team = foreign_team if failing_conjunct == "link-team" else team
        tag = TagFactory.create(team=foreign_team if failing_conjunct == "tag-team" else team)
        CustomTaggedItemFactory.create(team=link_team, tag=tag, target=message)

        data = DashboardService(team).get_tag_analytics_data()

        assert data["tag_categories"] == {}
        assert data["total_tagged_messages"] == 0

    def test_foreign_teams_tag_never_renders_in_the_breakdown(self, team, chat):
        """The reported shape (see the PR demo): a `CustomTaggedItem` owned
        by another team, carrying that team's tag, points at one of this
        team's messages. Both conjuncts fail at once, so the conjunct case
        list above already rejects it; this test pins the user-visible
        property instead - the breakdown renders the reading team's tags and
        nothing else, so the foreign tag's user-authored name never appears
        beside them."""
        secret = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="hi")
        foreign_team = TeamFactory.create()
        foreign_tag = TagFactory.create(team=foreign_team, name="OTHER-TEAMS-SECRET-TAG")
        CustomTaggedItemFactory.create(team=foreign_team, tag=foreign_tag, target=secret)
        ours = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="ours")
        local_tag = TagFactory.create(team=team, name="our-own-tag")
        CustomTaggedItemFactory.create(team=team, tag=local_tag, target=ours)

        data = DashboardService(team).get_tag_analytics_data()

        assert data["tag_categories"] == {local_tag.label: {local_tag.name: 1}}
        assert data["total_tagged_messages"] == 1

    def test_own_teams_link_and_tag_are_counted(self, team, chat):
        message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="hi")
        tag = TagFactory.create(team=team, name="our-own-tag")
        CustomTaggedItemFactory.create(team=team, tag=tag, target=message)

        data = DashboardService(team).get_tag_analytics_data()

        assert data["tag_categories"] == {tag.label: {tag.name: 1}}
        assert data["total_tagged_messages"] == 1


@pytest.mark.django_db()
class TestUserEngagementUsesTheCanonicalDefinitions:
    """The engagement panel sits on the same page as the headline totals, so it
    counts the same universe: no SETUP sessions, no evaluation-harness sessions,
    and a tag filter narrows it the same way (ADR-0051)."""

    def _totals(self, data):
        return [(row["total_messages"], row["total_sessions"]) for row in data["most_active_participants"]]

    def test_setup_sessions_do_not_count(self, team, experiment, participant):
        _engagement_session(team, experiment, participant, messages=2)
        _engagement_session(team, experiment, participant, status=SessionStatus.SETUP, messages=3)

        data = DashboardService(team).get_user_engagement_data()

        assert self._totals(data) == [(2, 1)]

    def test_evaluation_sessions_do_not_count(self, team, experiment, participant):
        _engagement_session(team, experiment, participant, messages=2)
        _engagement_session(team, experiment, participant, platform=ChannelPlatform.EVALUATIONS, messages=3)

        data = DashboardService(team).get_user_engagement_data()

        assert self._totals(data) == [(2, 1)]

    def test_a_tag_filter_narrows_the_panel(self, team, experiment, participant):
        tagged = _engagement_session(team, experiment, participant, messages=2)
        _engagement_session(team, experiment, participant, messages=3)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=tagged.chat)

        data = DashboardService(team).get_user_engagement_data(tag_ids=[tag.id])

        assert self._totals(data) == [(2, 1)]

    def test_last_activity_comes_from_in_window_activity(self, team, experiment, participant):
        """`last_activity_at` is the session's own clock and carries no window
        bound, so reading it directly can report a date outside the range the
        page is showing."""
        session = _engagement_session(team, experiment, participant, messages=1)
        ExperimentSession.objects.filter(id=session.id).update(last_activity_at=timezone.now() + timedelta(days=40))

        data = DashboardService(team).get_user_engagement_data()

        message = ChatMessage.objects.get(chat=session.chat)
        assert data["most_active_participants"][0]["last_activity"] == message.created_at.isoformat()


def _engagement_session(team, experiment, participant, *, status=SessionStatus.ACTIVE, platform="web", messages=1):
    session = ExperimentSession.objects.create(
        experiment=experiment, participant=participant, team=team, status=status, platform=platform
    )
    for index in range(messages):
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content=f"m{index}")
    return session


def _create_session(experiment, participant, team, message_date):
    session = ExperimentSession.objects.create(
        experiment=experiment, participant=participant, team=team, status=SessionStatus.ACTIVE
    )
    message = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN)
    message.created_at = message_date
    message.save()
    return session
