import pytest
from datetime import date, timedelta
from django.utils import timezone
from django.test import TestCase

from apps.teams.models import Team
from apps.users.models import CustomUser
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.channels.models import ExperimentChannel

from ..models import DashboardCache, DashboardFilter, DashboardMetricsSnapshot


@pytest.mark.django_db
class TestDashboardCache:
    """Test dashboard cache functionality"""
    
    def test_cache_data_storage_and_retrieval(self, team):
        """Test basic cache storage and retrieval"""
        cache_key = 'test_key'
        test_data = {'metric': 'value', 'count': 123}
        
        # Store data
        DashboardCache.set_cached_data(team, cache_key, test_data, ttl_minutes=10)
        
        # Retrieve data
        retrieved_data = DashboardCache.get_cached_data(team, cache_key)
        
        assert retrieved_data == test_data
    
    def test_cache_expiry(self, team):
        """Test cache expiry functionality"""
        cache_key = 'expire_test'
        test_data = {'expires': True}
        
        # Store data with very short TTL
        cache_entry = DashboardCache.set_cached_data(team, cache_key, test_data, ttl_minutes=0)
        
        # Manually set expiry to past
        cache_entry.expires_at = timezone.now() - timedelta(minutes=1)
        cache_entry.save()
        
        # Should return None for expired data
        retrieved_data = DashboardCache.get_cached_data(team, cache_key)
        assert retrieved_data is None
    
    def test_cache_key_uniqueness_per_team(self, team, experiment_team):
        """Test that cache keys are unique per team"""
        cache_key = 'same_key'
        team1_data = {'team': 'team1'}
        team2_data = {'team': 'team2'}
        
        # Store same key for different teams
        DashboardCache.set_cached_data(team, cache_key, team1_data)
        DashboardCache.set_cached_data(experiment_team, cache_key, team2_data)
        
        # Retrieve should return team-specific data
        team1_retrieved = DashboardCache.get_cached_data(team, cache_key)
        team2_retrieved = DashboardCache.get_cached_data(experiment_team, cache_key)
        
        assert team1_retrieved == team1_data
        assert team2_retrieved == team2_data


@pytest.mark.django_db
class TestDashboardFilter:
    """Test dashboard filter functionality"""
    
    def test_filter_creation(self, team, user):
        """Test creating filter presets"""
        filter_data = {
            'date_range': '30',
            'experiments': [1, 2, 3],
            'granularity': 'daily'
        }
        
        dashboard_filter = DashboardFilter.objects.create(
            team=team,
            user=user,
            filter_name='Test Filter',
            filter_data=filter_data,
            is_default=False
        )
        
        assert dashboard_filter.filter_name == 'Test Filter'
        assert dashboard_filter.filter_data == filter_data
        assert dashboard_filter.team == team
        assert dashboard_filter.user == user
    
    def test_default_filter_uniqueness(self, team, user):
        """Test that only one default filter per user/team"""
        # Create first default filter
        filter1 = DashboardFilter.objects.create(
            team=team,
            user=user,
            filter_name='Default 1',
            filter_data={'test': 1},
            is_default=True
        )
        
        # Create second default filter
        filter2 = DashboardFilter.objects.create(
            team=team,
            user=user,
            filter_name='Default 2',
            filter_data={'test': 2},
            is_default=True
        )
        
        # First filter should no longer be default
        filter1.refresh_from_db()
        assert not filter1.is_default
        assert filter2.is_default


@pytest.mark.django_db
class TestDashboardMetricsSnapshot:
    """Test dashboard metrics snapshot functionality"""
    
    def test_snapshot_generation(self, team, experiment, participant, experiment_session, chat, user):
        """Test basic snapshot generation"""
        # Create some test messages
        ChatMessage.objects.create(
            chat=chat,
            message_type=ChatMessageType.HUMAN,
            content='Test human message'
        )
        ChatMessage.objects.create(
            chat=chat,
            message_type=ChatMessageType.AI,
            content='Test AI message'
        )
        
        # Generate snapshot for today
        today = timezone.now().date()
        snapshot = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        assert snapshot is not None
        assert snapshot.team == team
        assert snapshot.date == today
        assert snapshot.total_experiments >= 1
        assert snapshot.total_participants >= 1
        assert snapshot.total_sessions >= 1
        assert snapshot.total_messages >= 2
    
    def test_snapshot_uniqueness_per_team_date(self, team):
        """Test that snapshots are unique per team and date"""
        today = timezone.now().date()
        
        # Generate first snapshot
        snapshot1 = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        # Generate second snapshot for same team and date
        snapshot2 = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        # Should be the same object (updated, not created new)
        assert snapshot1.id == snapshot2.id
    
    def test_snapshot_with_no_data(self, team):
        """Test snapshot generation when no data exists"""
        today = timezone.now().date()
        snapshot = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        assert snapshot.total_experiments == 0
        assert snapshot.total_participants == 0
        assert snapshot.total_sessions == 0
        assert snapshot.total_messages == 0
        assert snapshot.active_experiments == 0
        assert snapshot.active_participants == 0
    
    def test_channel_stats_aggregation(self, team, experiment, participant, experiment_channel):
        """Test channel statistics aggregation in snapshots"""
        # Create session with channel
        session = ExperimentSession.objects.create(
            experiment=experiment,
            participant=participant,
            team=team,
            experiment_channel=experiment_channel
        )
        
        # Create chat for session
        chat = Chat.objects.create(team=team, name='Test Chat')
        session.chat = chat
        session.save()
        
        # Create message
        ChatMessage.objects.create(
            chat=chat,
            message_type=ChatMessageType.HUMAN,
            content='Test message'
        )
        
        today = timezone.now().date()
        snapshot = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        # Check channel stats
        assert snapshot.channel_stats is not None
        assert experiment_channel.platform in snapshot.channel_stats
        
        channel_stat = snapshot.channel_stats[experiment_channel.platform]
        assert channel_stat['sessions'] >= 1
        assert channel_stat['messages'] >= 1
        assert channel_stat['participants'] >= 1


@pytest.mark.django_db
class TestDashboardMetricsCalculations:
    """Test various dashboard metric calculations"""
    
    def test_session_duration_calculation(self, team, experiment, participant):
        """Test session duration calculations"""
        # Create session with specific start and end times
        start_time = timezone.now() - timedelta(hours=2)
        end_time = timezone.now() - timedelta(hours=1)
        
        session = ExperimentSession.objects.create(
            experiment=experiment,
            participant=participant,
            team=team,
            created_at=start_time,
            ended_at=end_time
        )
        
        # Create chat for session
        chat = Chat.objects.create(team=team, name='Test Chat')
        session.chat = chat
        session.save()
        
        today = timezone.now().date()
        snapshot = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        # Session duration should be approximately 60 minutes
        assert snapshot.avg_session_duration_minutes is not None
        assert 55 <= snapshot.avg_session_duration_minutes <= 65  # Allow some variance
    
    def test_completion_rate_calculation(self, team, experiment, participant):
        """Test session completion rate calculations"""
        # Create completed session
        completed_session = ExperimentSession.objects.create(
            experiment=experiment,
            participant=participant,
            team=team,
            ended_at=timezone.now()
        )
        
        # Create incomplete session (no end time)
        incomplete_session = ExperimentSession.objects.create(
            experiment=experiment,
            participant=participant,
            team=team
        )
        
        # Create chats for sessions
        for session in [completed_session, incomplete_session]:
            chat = Chat.objects.create(team=team, name=f'Test Chat {session.id}')
            session.chat = chat
            session.save()
        
        today = timezone.now().date()
        snapshot = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        # Completion rate should be 50% (1 of 2 sessions completed)
        assert snapshot.session_completion_rate is not None
        assert 0.4 <= snapshot.session_completion_rate <= 0.6  # Allow some variance
    
    def test_messages_per_session_calculation(self, team, experiment, participant):
        """Test average messages per session calculation"""
        # Create session with specific message count
        session = ExperimentSession.objects.create(
            experiment=experiment,
            participant=participant,
            team=team
        )
        
        chat = Chat.objects.create(team=team, name='Test Chat')
        session.chat = chat
        session.save()
        
        # Create 5 messages
        for i in range(5):
            ChatMessage.objects.create(
                chat=chat,
                message_type=ChatMessageType.HUMAN,
                content=f'Test message {i}'
            )
        
        today = timezone.now().date()
        snapshot = DashboardMetricsSnapshot.generate_snapshot(team, today)
        
        # Should be 5 messages per session
        assert snapshot.avg_messages_per_session == 5.0