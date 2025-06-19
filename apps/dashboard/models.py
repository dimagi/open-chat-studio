from django.db import models
from django.utils import timezone
from datetime import timedelta

from apps.teams.models import Team
from apps.experiments.models import Experiment


class DashboardCache(models.Model):
    """Cache computed dashboard metrics to improve performance"""
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    cache_key = models.CharField(max_length=255)
    data = models.JSONField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('team', 'cache_key')
        indexes = [
            models.Index(fields=['team', 'cache_key', 'expires_at']),
        ]

    @classmethod
    def get_cached_data(cls, team, cache_key):
        """Get cached data if not expired"""
        try:
            cache_entry = cls.objects.get(
                team=team, 
                cache_key=cache_key, 
                expires_at__gt=timezone.now()
            )
            return cache_entry.data
        except cls.DoesNotExist:
            return None

    @classmethod
    def set_cached_data(cls, team, cache_key, data, ttl_minutes=30):
        """Cache data with TTL"""
        expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
        cache_entry, created = cls.objects.update_or_create(
            team=team,
            cache_key=cache_key,
            defaults={
                'data': data,
                'expires_at': expires_at,
            }
        )
        return cache_entry


class DashboardFilter(models.Model):
    """Store user's dashboard filter preferences"""
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    user = models.ForeignKey('users.CustomUser', on_delete=models.CASCADE)
    filter_name = models.CharField(max_length=100)  # e.g., 'date_range', 'experiments', 'channels'
    filter_data = models.JSONField()  # Store filter parameters
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('team', 'user', 'filter_name')
        indexes = [
            models.Index(fields=['team', 'user', 'filter_name']),
        ]


class DashboardMetricsSnapshot(models.Model):
    """Daily snapshot of key metrics for trend analysis"""
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    date = models.DateField()
    
    # Aggregate metrics
    total_experiments = models.IntegerField(default=0)
    total_participants = models.IntegerField(default=0)
    total_sessions = models.IntegerField(default=0)
    total_messages = models.IntegerField(default=0)
    active_experiments = models.IntegerField(default=0)
    active_participants = models.IntegerField(default=0)
    
    # Daily activity
    new_participants = models.IntegerField(default=0)
    new_sessions = models.IntegerField(default=0)
    messages_sent = models.IntegerField(default=0)
    human_messages = models.IntegerField(default=0)
    ai_messages = models.IntegerField(default=0)
    
    # Channel breakdown (JSON for flexibility)
    channel_stats = models.JSONField(default=dict)
    
    # Performance metrics
    avg_session_duration_minutes = models.FloatField(null=True, blank=True)
    avg_messages_per_session = models.FloatField(null=True, blank=True)
    session_completion_rate = models.FloatField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('team', 'date')
        indexes = [
            models.Index(fields=['team', 'date']),
            models.Index(fields=['date']),
        ]
        ordering = ['-date']

    @classmethod
    def generate_snapshot(cls, team, date=None):
        """Generate metrics snapshot for a specific date"""
        from apps.experiments.models import ExperimentSession, Participant
        from apps.chat.models import ChatMessage, ChatMessageType
        from apps.channels.models import ExperimentChannel
        from django.db.models import Count, Avg, Q
        from django.db.models.functions import TruncDate
        
        if date is None:
            date = timezone.now().date()
        
        # Get all experiments for the team
        experiments = Experiment.objects.filter(team=team, is_archived=False)
        
        # Get sessions and messages for the date
        sessions = ExperimentSession.objects.filter(
            team=team,
            created_at__date=date
        )
        
        all_sessions = ExperimentSession.objects.filter(team=team)
        all_participants = Participant.objects.filter(team=team)
        all_messages = ChatMessage.objects.filter(chat__team=team)
        
        # Messages for the specific date
        daily_messages = all_messages.filter(created_at__date=date)
        
        # Channel statistics
        channels = ExperimentChannel.objects.filter(team=team, deleted=False)
        channel_stats = {}
        for channel in channels:
            platform = channel.platform
            if platform not in channel_stats:
                channel_stats[platform] = {
                    'sessions': 0,
                    'messages': 0,
                    'participants': 0
                }
            
            platform_sessions = sessions.filter(experiment_channel=channel)
            platform_messages = daily_messages.filter(
                chat__experiment_session__experiment_channel=channel
            )
            platform_participants = platform_sessions.values('participant').distinct()
            
            channel_stats[platform]['sessions'] += platform_sessions.count()
            channel_stats[platform]['messages'] += platform_messages.count()
            channel_stats[platform]['participants'] += platform_participants.count()
        
        # Calculate performance metrics
        completed_sessions = sessions.filter(ended_at__isnull=False)
        avg_duration = None
        if completed_sessions.exists():
            durations = []
            for session in completed_sessions:
                if session.ended_at and session.created_at:
                    duration = session.ended_at - session.created_at
                    durations.append(duration.total_seconds() / 60)
            avg_duration = sum(durations) / len(durations) if durations else None
        
        # Messages per session
        session_msg_counts = sessions.annotate(
            msg_count=Count('chat__messages')
        ).aggregate(avg_messages=Avg('msg_count'))
        
        completion_rate = None
        total_sessions_count = sessions.count()
        if total_sessions_count > 0:
            completed_count = completed_sessions.count()
            completion_rate = completed_count / total_sessions_count
        
        # Create or update snapshot
        snapshot, created = cls.objects.update_or_create(
            team=team,
            date=date,
            defaults={
                'total_experiments': experiments.count(),
                'total_participants': all_participants.count(),
                'total_sessions': all_sessions.count(),
                'total_messages': all_messages.count(),
                'active_experiments': experiments.filter(
                    sessions__created_at__date=date
                ).distinct().count(),
                'active_participants': all_participants.filter(
                    experimentsession__created_at__date=date
                ).distinct().count(),
                'new_participants': all_participants.filter(
                    created_at__date=date
                ).count(),
                'new_sessions': sessions.count(),
                'messages_sent': daily_messages.count(),
                'human_messages': daily_messages.filter(
                    message_type=ChatMessageType.HUMAN
                ).count(),
                'ai_messages': daily_messages.filter(
                    message_type=ChatMessageType.AI
                ).count(),
                'channel_stats': channel_stats,
                'avg_session_duration_minutes': avg_duration,
                'avg_messages_per_session': session_msg_counts['avg_messages'],
                'session_completion_rate': completion_rate,
            }
        )
        
        return snapshot