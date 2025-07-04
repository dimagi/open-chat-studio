from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession

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

        old_session = ExperimentSession.objects.create(experiment=experiment, participant=participant, team=team)
        message = ChatMessage.objects.create(
            chat=old_session.chat, message_type=ChatMessageType.HUMAN, content="Old session message"
        )
        message.created_at = old_date
        message.save()

        recent_session = ExperimentSession.objects.create(experiment=experiment, participant=participant, team=team)
        message = ChatMessage.objects.create(
            chat=recent_session.chat, message_type=ChatMessageType.HUMAN, content="New session message"
        )
        message.created_at = recent_date
        message.save()

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
        session1 = ExperimentSession.objects.create(experiment=experiment, participant=participant, team=team)
        session1.chat.messages.create(message_type=ChatMessageType.HUMAN, content="Human message")

        session2 = ExperimentSession.objects.create(experiment=other_experiment, participant=participant, team=team)
        session2.chat.messages.create(message_type=ChatMessageType.HUMAN, content="Human message")

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

    def test_get_active_participants_data(self, team, experiment, participant, experiment_session, chat):
        """Test active participants data generation"""
        service = DashboardService(team)

        # Create human message (makes participant active)
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test message")

        data = service.get_active_participants_data(granularity="daily")

        assert isinstance(data, list)
        if data:  # If there's data
            item = data[0]
            assert "date" in item
            assert "active_participants" in item
            assert item["active_participants"] >= 1

    def test_get_session_analytics_data(self, team, experiment, participant, experiment_session):
        """Test session analytics data generation"""
        service = DashboardService(team)

        data = service.get_session_analytics_data(granularity="daily")

        assert isinstance(data, dict)
        assert "sessions" in data
        assert "participants" in data
        assert isinstance(data["sessions"], list)
        assert isinstance(data["participants"], list)

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
            expected_fields = ["platform", "platform_name", "sessions", "messages", "participants"]
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
        assert "most_active_participants" in data
        assert "least_active_participants" in data
        assert "session_length_distribution" in data
        assert "total_participants" in data

        assert isinstance(data["most_active_participants"], list)
        assert isinstance(data["least_active_participants"], list)
        assert isinstance(data["session_length_distribution"], list)

    def test_granularity_options(self, team, experiment, participant, experiment_session, chat):
        """Test different granularity options"""
        service = DashboardService(team)

        # Create test message
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test message")

        granularities = ["hourly", "daily", "weekly", "monthly"]

        for granularity in granularities:
            data = service.get_active_participants_data(granularity=granularity)
            assert isinstance(data, list)

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

        participants_data = service.get_active_participants_data()
        assert isinstance(participants_data, list)

        session_data = service.get_session_analytics_data()
        assert isinstance(session_data, dict)

        performance_data = service.get_bot_performance_summary()
        assert isinstance(performance_data["results"], list)

    def test_caching_behavior(self, team, experiment, participant, experiment_session, chat):
        """Test caching behavior in service methods"""
        from ..models import DashboardCache

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
