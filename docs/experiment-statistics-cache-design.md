# Experiment Statistics Cache System - Design Document

## Overview

This document describes the design for a caching system to improve the performance of experiment and session statistics queries in Open Chat Studio. The current implementation uses expensive subqueries that cause slow page load times, particularly for the chatbot table view.

## Problem Statement

The `ChatbotExperimentTableView` in `apps/chatbots/views.py` (lines 163-238) performs expensive subqueries to calculate statistics for each experiment:

- **Experiment Level**:
  - Total session count
  - Participant count (unique participants)
  - Message count (human messages only)
  - Last activity timestamp (last human message)

- **Session Level** (used in `ChatbotSessionsTableView`):
  - Message count (human messages)
  - Last activity timestamp (last human message)
  - List of experiment version numbers used

These queries are executed on every page load and become increasingly slow as data volume grows.

## Design Goals

1. **Performance**: Reduce query time from seconds to milliseconds
2. **Accuracy**: Balance freshness with performance (near real-time for recent data)
3. **Scalability**: Handle growing data volume through periodic compression
4. **Maintainability**: Simple, understandable code following Django patterns
5. **Reliability**: Graceful degradation if cache is stale or missing

## Proposed Solution

### Architecture Overview

A two-tier caching system with periodic aggregation:

1. **SQL-based cache tables** storing pre-computed statistics
2. **Periodic background tasks** to update and compress statistics
3. **Hybrid update strategy** combining scheduled updates with live updates for recent data

## Database Schema

### 1. Experiment Statistics Cache

```python
# apps/experiments/models.py

class ExperimentStatistics(BaseTeamModel):
    """
    Cached statistics for experiments, updated periodically.
    """

    experiment = models.OneToOneField(
        Experiment,
        on_delete=models.CASCADE,
        related_name='cached_statistics',
        primary_key=True
    )

    # Aggregate statistics
    total_session_count = models.IntegerField(default=0)
    participant_count = models.IntegerField(default=0)
    human_message_count = models.IntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    last_updated_at = models.DateTimeField(auto_now=True, db_index=True)
    last_full_refresh_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'experiments_experiment_statistics'
        indexes = [
            models.Index(fields=['experiment', 'last_updated_at']),
            models.Index(fields=['last_activity_at']),
        ]

    def __str__(self):
        return f"Stats for {self.experiment.name}"
```

### 2. Session Statistics Cache

```python
# apps/experiments/models.py

class SessionStatistics(BaseTeamModel):
    """
    Cached statistics for experiment sessions, updated periodically.
    """

    session = models.OneToOneField(
        ExperimentSession,
        on_delete=models.CASCADE,
        related_name='cached_statistics',
        primary_key=True
    )

    # Aggregate statistics
    human_message_count = models.IntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    experiment_versions = models.CharField(max_length=500, blank=True)

    # Metadata
    last_updated_at = models.DateTimeField(auto_now=True, db_index=True)
    is_complete = models.BooleanField(
        default=False,
        help_text="Whether this session is complete and won't receive more updates"
    )

    class Meta:
        db_table = 'experiments_session_statistics'
        indexes = [
            models.Index(fields=['session', 'last_updated_at']),
            models.Index(fields=['last_activity_at']),
            models.Index(fields=['is_complete']),
        ]

    def __str__(self):
        return f"Stats for Session {self.session.external_id}"
```

### 3. Statistics Update Log (Optional, for debugging)

```python
# apps/experiments/models.py

class StatisticsUpdateLog(BaseModel):
    """
    Log of statistics update operations for monitoring and debugging.
    Optional - can be added if needed for troubleshooting.
    """

    update_type = models.CharField(
        max_length=50,
        choices=[
            ('full_refresh', 'Full Refresh'),
            ('incremental', 'Incremental Update'),
            ('compression', 'Compression'),
        ]
    )
    scope = models.CharField(
        max_length=50,
        choices=[
            ('all_experiments', 'All Experiments'),
            ('single_experiment', 'Single Experiment'),
            ('all_sessions', 'All Sessions'),
            ('single_session', 'Single Session'),
        ]
    )

    experiments_updated = models.IntegerField(default=0)
    sessions_updated = models.IntegerField(default=0)
    duration_seconds = models.FloatField()
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'experiments_statistics_update_log'
        indexes = [
            models.Index(fields=['-created_at']),
        ]
```

## Update Strategies

### Strategy 1: Scheduled Full Refresh (Recommended Starting Point)

**Approach**: Periodically recalculate all statistics from scratch.

**Pros**:
- Simple to implement and understand
- Always accurate (no drift)
- Easy to debug and verify
- No complex incremental logic

**Cons**:
- Less efficient for large datasets
- Statistics can be stale between refreshes
- May be heavy on database during refresh

**Implementation**:
```python
# apps/experiments/tasks.py

@shared_task
def refresh_experiment_statistics(experiment_id=None, full_refresh=True):
    """
    Refresh statistics for experiments.

    Args:
        experiment_id: If provided, refresh only this experiment. Otherwise, all.
        full_refresh: If True, recalculate from scratch. If False, incremental.
    """
    from django.db.models import Count, Max, Q
    from apps.experiments.models import (
        Experiment, ExperimentStatistics, ExperimentSession
    )
    from apps.chat.models import ChatMessage, ChatMessageType

    # Determine which experiments to update
    if experiment_id:
        experiments = Experiment.objects.filter(id=experiment_id)
    else:
        experiments = Experiment.objects.filter(
            working_version__isnull=True,
            is_archived=False
        )

    for experiment in experiments.iterator(chunk_size=100):
        # Calculate statistics using efficient aggregation
        session_stats = ExperimentSession.objects.filter(
            experiment=experiment
        ).aggregate(
            total_sessions=Count('id'),
            unique_participants=Count('participant', distinct=True)
        )

        # Get message statistics
        message_stats = ChatMessage.objects.filter(
            chat__experiment_session__experiment=experiment,
            message_type=ChatMessageType.HUMAN
        ).aggregate(
            total_messages=Count('id'),
            last_message=Max('created_at')
        )

        # Update or create cache entry
        ExperimentStatistics.objects.update_or_create(
            experiment=experiment,
            defaults={
                'total_session_count': session_stats['total_sessions'] or 0,
                'participant_count': session_stats['unique_participants'] or 0,
                'human_message_count': message_stats['total_messages'] or 0,
                'last_activity_at': message_stats['last_message'],
                'last_full_refresh_at': timezone.now(),
            }
        )

@shared_task
def refresh_session_statistics(session_id=None, mark_complete=False):
    """
    Refresh statistics for experiment sessions.

    Args:
        session_id: If provided, refresh only this session. Otherwise, all active.
        mark_complete: Whether to mark the session as complete (no more updates).
    """
    from django.db.models import Count, Max
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.postgres.aggregates import StringAgg
    from apps.experiments.models import (
        ExperimentSession, SessionStatistics
    )
    from apps.chat.models import Chat, ChatMessage, ChatMessageType
    from apps.annotations.models import CustomTaggedItem

    # Determine which sessions to update
    if session_id:
        sessions = ExperimentSession.objects.filter(id=session_id)
    else:
        # Update only active sessions (not complete)
        sessions = ExperimentSession.objects.exclude(
            cached_statistics__is_complete=True
        )

    message_ct = ContentType.objects.get_for_model(ChatMessage)

    for session in sessions.iterator(chunk_size=500):
        # Get message statistics
        message_stats = ChatMessage.objects.filter(
            chat=session.chat,
            message_type=ChatMessageType.HUMAN
        ).aggregate(
            total_messages=Count('id'),
            last_message=Max('created_at')
        )

        # Get experiment versions used
        versions = CustomTaggedItem.objects.filter(
            content_type=message_ct,
            object_id__in=ChatMessage.objects.filter(
                chat=session.chat
            ).values('id'),
            tag__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
        ).values_list('tag__name', flat=True).distinct().order_by('tag__name')

        versions_str = ', '.join(versions) if versions else ''

        # Determine if session is complete
        is_complete = mark_complete or (
            session.status == SessionStatus.COMPLETE
        )

        # Update or create cache entry
        SessionStatistics.objects.update_or_create(
            session=session,
            defaults={
                'human_message_count': message_stats['total_messages'] or 0,
                'last_activity_at': message_stats['last_message'],
                'experiment_versions': versions_str,
                'is_complete': is_complete,
            }
        )
```

**Schedule Configuration**:
```python
# config/settings.py

SCHEDULED_TASKS = {
    # ... existing tasks ...

    "experiments.tasks.refresh_all_experiment_statistics": {
        "task": "apps.experiments.tasks.refresh_all_experiment_statistics",
        "schedule": timedelta(minutes=5),  # Every 5 minutes
    },
    "experiments.tasks.refresh_active_session_statistics": {
        "task": "apps.experiments.tasks.refresh_active_session_statistics",
        "schedule": timedelta(minutes=2),  # Every 2 minutes for active sessions
    },
    "experiments.tasks.cleanup_old_statistics_logs": {
        "task": "apps.experiments.tasks.cleanup_old_statistics_logs",
        "schedule": timedelta(days=7),  # Weekly cleanup
    },
}
```

### Strategy 2: Incremental Updates (Future Enhancement)

**Approach**: Track changes and update only affected statistics.

**Pros**:
- More efficient for large datasets
- Fresher data
- Lower database load per update

**Cons**:
- More complex implementation
- Risk of drift over time
- Requires change tracking
- Harder to debug

**Implementation Notes**:
- Use Django signals on `ChatMessage` creation to update session statistics
- Use Django signals on `ExperimentSession` creation to update experiment statistics
- Combine with periodic full refresh to prevent drift
- Add `last_full_refresh_at` timestamp to detect drift

**Signal Example**:
```python
# apps/experiments/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.chat.models import ChatMessage, ChatMessageType

@receiver(post_save, sender=ChatMessage)
def update_statistics_on_message(sender, instance, created, **kwargs):
    """Update statistics when a new human message is created."""
    if not created or instance.message_type != ChatMessageType.HUMAN:
        return

    # Queue a task to update session and experiment statistics
    from apps.experiments.tasks import update_statistics_incremental
    update_statistics_incremental.delay(
        session_id=instance.chat.experiment_session_id
    )
```

### Strategy 3: Hybrid Approach (Recommended for Scale)

**Approach**: Combine scheduled refresh for old data with live updates for recent data.

**Pros**:
- Best balance of performance and freshness
- Scalable to large datasets
- Recent data always fresh
- Historical data updated periodically

**Cons**:
- Most complex to implement
- Requires careful coordination

**Implementation**:
```python
@shared_task
def hybrid_refresh_statistics():
    """
    Hybrid refresh strategy:
    1. Live update active sessions (last 24 hours)
    2. Periodic refresh for older sessions
    3. Full refresh weekly
    """
    from datetime import timedelta
    from django.utils import timezone

    cutoff = timezone.now() - timedelta(hours=24)

    # Update recent sessions immediately
    recent_sessions = ExperimentSession.objects.filter(
        created_at__gte=cutoff
    ).values_list('id', flat=True)

    for session_id in recent_sessions:
        refresh_session_statistics.delay(session_id)

    # Update experiments with recent activity
    recent_experiments = Experiment.objects.filter(
        sessions__created_at__gte=cutoff
    ).distinct().values_list('id', flat=True)

    for exp_id in recent_experiments:
        refresh_experiment_statistics.delay(exp_id)
```

## Data Compression Strategy

As data volume grows, older statistics can be "compressed" to reduce storage:

### Time-based Compression

**Approach**: Delete or archive session statistics for very old, completed sessions.

**Implementation**:
```python
@shared_task
def compress_old_statistics():
    """
    Archive or delete statistics for old completed sessions.
    Keep experiment-level aggregates, remove session-level detail.
    """
    from datetime import timedelta
    from django.utils import timezone
    from apps.experiments.models import SessionStatistics

    # Define retention policy: keep session details for 90 days
    cutoff = timezone.now() - timedelta(days=90)

    # Delete session statistics older than cutoff
    # Experiment statistics remain intact
    old_stats = SessionStatistics.objects.filter(
        is_complete=True,
        session__ended_at__lt=cutoff
    )

    count = old_stats.count()
    old_stats.delete()

    logger.info(f"Compressed {count} old session statistics")
```

### Aggregation Compression

**Approach**: For very old experiments, aggregate statistics by time period.

**Schema** (optional):
```python
class ExperimentStatisticsArchive(BaseTeamModel):
    """
    Time-bucketed historical statistics for experiments.
    Used for long-term trends without session-level detail.
    """

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE)
    period_start = models.DateField()
    period_end = models.DateField()
    period_type = models.CharField(
        max_length=20,
        choices=[('day', 'Daily'), ('week', 'Weekly'), ('month', 'Monthly')]
    )

    session_count = models.IntegerField()
    participant_count = models.IntegerField()
    message_count = models.IntegerField()

    class Meta:
        unique_together = ('experiment', 'period_start', 'period_type')
```

## View Integration

### Update `ChatbotExperimentTableView`

**Before** (current slow query):
```python
def get_table_data(self):
    queryset = super().get_table_data()

    # Expensive subqueries
    queryset = queryset.annotate(
        session_count=Subquery(session_count_subquery, output_field=IntegerField()),
        participant_count=Subquery(participant_count_subquery, output_field=IntegerField()),
        messages_count=Subquery(messages_count_subquery, output_field=IntegerField()),
        last_message=Subquery(last_message_subquery, output_field=DateTimeField()),
    ).order_by(F("last_message").desc(nulls_last=True))
    return queryset
```

**After** (using cache):
```python
def get_table_data(self):
    queryset = super().get_table_data()

    # Use cached statistics
    queryset = queryset.select_related('cached_statistics').annotate(
        session_count=F('cached_statistics__total_session_count'),
        participant_count=F('cached_statistics__participant_count'),
        messages_count=F('cached_statistics__human_message_count'),
        last_message=F('cached_statistics__last_activity_at'),
    ).order_by(F("last_message").desc(nulls_last=True))
    return queryset
```

### Update `ChatbotSessionsTableView`

**Before**:
```python
def get_table_data(self):
    queryset = super().get_table_data()
    return queryset.annotate_with_message_count().annotate_with_last_message_created_at()
```

**After**:
```python
def get_table_data(self):
    queryset = super().get_table_data()
    return queryset.select_related('cached_statistics').annotate(
        message_count=F('cached_statistics__human_message_count'),
        last_message_created_at=F('cached_statistics__last_activity_at'),
        experiment_versions=F('cached_statistics__experiment_versions'),
    )
```

## Graceful Degradation

Handle missing or stale cache entries gracefully:

```python
# apps/experiments/utils.py

def get_experiment_statistics(experiment):
    """
    Get statistics for an experiment, using cache if available,
    falling back to live calculation if not.
    """
    try:
        stats = experiment.cached_statistics

        # Check if cache is too old (> 1 hour)
        if stats.last_updated_at < timezone.now() - timedelta(hours=1):
            # Queue refresh but return cached data
            from apps.experiments.tasks import refresh_experiment_statistics
            refresh_experiment_statistics.delay(experiment.id)

        return stats
    except ExperimentStatistics.DoesNotExist:
        # Cache missing - calculate live and queue cache creation
        from apps.experiments.tasks import refresh_experiment_statistics
        refresh_experiment_statistics.delay(experiment.id)

        # Return live calculation
        return calculate_experiment_statistics_live(experiment)

def calculate_experiment_statistics_live(experiment):
    """Fallback: calculate statistics without cache."""
    # Use the original subquery logic
    # Return a dict or object with the same interface
    pass
```

## Migration Plan

### Phase 1: Foundation (Week 1)
1. Create new models (`ExperimentStatistics`, `SessionStatistics`)
2. Write and test migration
3. Create basic refresh tasks
4. Add management command for manual refresh

### Phase 2: Integration (Week 2)
1. Update views to use cached statistics
2. Add graceful degradation logic
3. Set up periodic tasks
4. Monitor performance improvements

### Phase 3: Optimization (Week 3)
1. Fine-tune refresh intervals
2. Implement compression for old data
3. Add monitoring/alerting for cache staleness
4. Optimize queries based on real usage

### Phase 4: Enhancement (Future)
1. Implement incremental updates (signals)
2. Add hybrid refresh strategy
3. Implement statistics archive for historical trends
4. Add admin UI for cache management

## Management Commands

Provide CLI tools for managing the cache:

```python
# apps/experiments/management/commands/refresh_statistics.py

class Command(BaseCommand):
    help = 'Refresh experiment and session statistics cache'

    def add_arguments(self, parser):
        parser.add_argument(
            '--experiment-id',
            type=int,
            help='Refresh specific experiment'
        )
        parser.add_argument(
            '--session-id',
            type=int,
            help='Refresh specific session'
        )
        parser.add_argument(
            '--full',
            action='store_true',
            help='Full refresh of all statistics'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run refresh tasks asynchronously via Celery'
        )

    def handle(self, *args, **options):
        if options['async']:
            # Queue Celery tasks
            if options['experiment_id']:
                refresh_experiment_statistics.delay(options['experiment_id'])
            else:
                refresh_all_experiment_statistics.delay()
        else:
            # Run synchronously
            if options['experiment_id']:
                refresh_experiment_statistics(options['experiment_id'])
            else:
                refresh_all_experiment_statistics()
```

## Monitoring and Alerting

Track cache health and performance:

### Metrics to Monitor

1. **Cache Hit Rate**: % of requests using cache vs. live calculation
2. **Cache Staleness**: Age of cached data
3. **Refresh Duration**: Time taken to refresh statistics
4. **Query Performance**: Compare cached vs. non-cached query times
5. **Error Rate**: Failed cache updates

### Implementation

```python
# apps/experiments/monitoring.py

import logging
from django.utils import timezone

logger = logging.getLogger('ocs.experiments.cache')

def log_cache_performance(operation, duration, records_updated, errors=None):
    """Log cache operation performance."""
    logger.info(
        f"Cache {operation} completed",
        extra={
            'operation': operation,
            'duration_seconds': duration,
            'records_updated': records_updated,
            'errors': errors or [],
            'timestamp': timezone.now(),
        }
    )
```

### Admin Dashboard (Future)

```python
# apps/experiments/admin.py

@admin.register(ExperimentStatistics)
class ExperimentStatisticsAdmin(admin.ModelAdmin):
    list_display = [
        'experiment',
        'total_session_count',
        'participant_count',
        'human_message_count',
        'last_activity_at',
        'last_updated_at',
        'cache_age',
    ]
    list_filter = ['last_updated_at', 'last_full_refresh_at']
    search_fields = ['experiment__name']
    readonly_fields = [
        'last_updated_at',
        'last_full_refresh_at',
        'cache_age',
    ]

    def cache_age(self, obj):
        """Display age of cached data."""
        if obj.last_updated_at:
            age = timezone.now() - obj.last_updated_at
            return f"{age.total_seconds() / 60:.1f} minutes"
        return "Never"
    cache_age.short_description = "Cache Age"

    actions = ['refresh_statistics']

    def refresh_statistics(self, request, queryset):
        """Admin action to refresh selected statistics."""
        for stats in queryset:
            refresh_experiment_statistics.delay(stats.experiment_id)
        self.message_user(
            request,
            f"Queued refresh for {queryset.count()} experiments"
        )
    refresh_statistics.short_description = "Refresh selected statistics"
```

## Testing Strategy

### Unit Tests

```python
# apps/experiments/tests/test_statistics_cache.py

class TestExperimentStatistics(TestCase):
    def setUp(self):
        self.team = TeamFactory()
        self.experiment = ExperimentFactory(team=self.team)

    def test_statistics_calculation(self):
        """Test accurate statistics calculation."""
        # Create test data
        session1 = ExperimentSessionFactory(experiment=self.experiment)
        session2 = ExperimentSessionFactory(experiment=self.experiment)

        ChatMessageFactory.create_batch(
            5,
            chat=session1.chat,
            message_type=ChatMessageType.HUMAN
        )
        ChatMessageFactory.create_batch(
            3,
            chat=session2.chat,
            message_type=ChatMessageType.HUMAN
        )

        # Refresh statistics
        refresh_experiment_statistics(self.experiment.id)

        # Verify
        stats = self.experiment.cached_statistics
        assert stats.total_session_count == 2
        assert stats.human_message_count == 8

    def test_statistics_update_on_new_message(self):
        """Test incremental update when new message arrives."""
        # Test for Strategy 2/3
        pass

    def test_graceful_degradation(self):
        """Test fallback when cache is missing."""
        # Delete cache
        ExperimentStatistics.objects.all().delete()

        # Query should still work
        stats = get_experiment_statistics(self.experiment)
        assert stats is not None
```

### Performance Tests

```python
class TestCachePerformance(TestCase):
    def test_query_performance_improvement(self):
        """Verify cache improves query performance."""
        # Create large dataset
        experiments = ExperimentFactory.create_batch(100)
        for exp in experiments:
            sessions = ExperimentSessionFactory.create_batch(10, experiment=exp)
            for session in sessions:
                ChatMessageFactory.create_batch(
                    20,
                    chat=session.chat,
                    message_type=ChatMessageType.HUMAN
                )

        # Refresh cache
        refresh_all_experiment_statistics()

        # Measure uncached query
        with self.assertNumQueries(100):  # Many queries
            list(Experiment.objects.all().annotate(...))  # Old way

        # Measure cached query
        with self.assertNumQueries(1):  # Single query
            list(Experiment.objects.all().select_related('cached_statistics'))
```

## Configuration Options

Allow customization through Django settings:

```python
# config/settings.py

# Statistics Cache Configuration
STATISTICS_CACHE = {
    # Refresh intervals (in seconds)
    'EXPERIMENT_REFRESH_INTERVAL': 300,  # 5 minutes
    'SESSION_REFRESH_INTERVAL': 120,     # 2 minutes

    # Compression settings
    'SESSION_RETENTION_DAYS': 90,
    'ENABLE_COMPRESSION': True,

    # Update strategy
    'UPDATE_STRATEGY': 'scheduled',  # 'scheduled', 'incremental', 'hybrid'

    # Performance
    'BATCH_SIZE': 100,
    'REFRESH_TIMEOUT': 300,  # seconds

    # Monitoring
    'LOG_PERFORMANCE': True,
    'ALERT_ON_STALE_CACHE': True,
    'STALE_THRESHOLD_MINUTES': 60,
}
```

## Risks and Mitigations

### Risk 1: Cache Drift
**Issue**: Statistics become inaccurate over time.

**Mitigation**:
- Regular full refreshes (daily/weekly)
- Track `last_full_refresh_at` timestamp
- Monitoring and alerting for drift
- Validation tests comparing cache vs. live

### Risk 2: Database Load During Refresh
**Issue**: Full refresh may spike database load.

**Mitigation**:
- Use `.iterator()` for memory efficiency
- Process in batches
- Run during off-peak hours
- Add query timeouts
- Use database connection pooling

### Risk 3: Complexity
**Issue**: More code to maintain.

**Mitigation**:
- Start with simple Strategy 1
- Comprehensive testing
- Good documentation
- Management commands for manual control
- Graceful degradation

### Risk 4: Storage Growth
**Issue**: Cache tables grow over time.

**Mitigation**:
- Implement compression strategy
- Archive old statistics
- Define retention policies
- Monitor table sizes

## Performance Expectations

### Current Performance (Estimated)
- Table load time: 5-30 seconds (with 1000+ experiments)
- Database queries per page: 100+
- Memory usage: High (large result sets)

### Expected Performance (With Cache)
- Table load time: 0.5-2 seconds
- Database queries per page: 2-5
- Memory usage: Low (simple joins)

### ROI Calculation
```
Time saved per page load: ~10 seconds
Average page loads per day: 100
Daily time saved: 1,000 seconds (~17 minutes)
Weekly time saved: ~2 hours
```

## Alternative Approaches Considered

### 1. Redis Cache
**Pros**: Very fast, built-in TTL
**Cons**:
- Data not queryable
- Extra infrastructure
- Memory limits
- Doesn't solve filtering/sorting issues

### 2. Materialized Views
**Pros**: Database-native, automatic updates (with triggers)
**Cons**:
- PostgreSQL-specific
- Less flexible
- Harder to customize refresh logic
- Can't easily add metadata (compression, update logs)

### 3. Denormalized Columns on Experiment
**Pros**: Simpler schema
**Cons**:
- Clutters main model
- Harder to manage/update
- Can't track update metadata
- Limited flexibility for compression

## Recommendation

**Start with Strategy 1** (Scheduled Full Refresh):

1. **Phase 1**: Implement basic cache tables and scheduled refresh
2. **Measure**: Track performance improvements and cache hit rates
3. **Iterate**: Add incremental updates if needed
4. **Scale**: Implement compression when data volume requires it

This approach provides:
- ✅ Immediate performance improvements
- ✅ Simple, maintainable code
- ✅ Low risk
- ✅ Foundation for future enhancements

## Success Metrics

1. **Performance**: Table load time < 2 seconds
2. **Accuracy**: Cache never more than 5 minutes stale
3. **Reliability**: 99.9% uptime for cache refresh tasks
4. **Scalability**: Handles 10,000+ experiments without degradation

## References

- Current slow query: `apps/chatbots/views.py:175-238`
- Session annotations: `apps/experiments/models.py:1662-1700`
- Existing cache pattern: `apps/dashboard/models.py:9-46`
- Celery periodic tasks: `config/settings.py:460-494`
- Team-based model pattern: `apps/teams/models.py`

## Appendix: Sample Queries

### Current Slow Query
```python
# Experiment-level subquery (current)
messages_count_subquery = (
    ChatMessage.objects.filter(
        chat__experiment_session__experiment_id=OuterRef("pk")
    )
    .values("chat__experiment_session__experiment_id")
    .annotate(count=Count("id"))
    .values("count")
)
```

### Cached Query
```python
# Experiment-level with cache (proposed)
experiments = Experiment.objects.filter(
    team=team,
    working_version__isnull=True
).select_related('cached_statistics').annotate(
    session_count=F('cached_statistics__total_session_count'),
    messages_count=F('cached_statistics__human_message_count'),
)
```

### EXPLAIN ANALYZE Comparison

**Before** (estimated):
```
Seq Scan on experiments_experiment  (cost=0..5000.00 rows=1000)
  SubPlan 1
    ->  Aggregate  (cost=100..120)
          ->  Seq Scan on experiments_session  (cost=0..100)
  SubPlan 2
    ->  Aggregate  (cost=500..550)
          ->  Nested Loop  (cost=0..500)
                ->  Seq Scan on experiments_session
                ->  Seq Scan on chat_chatmessage
Planning time: 5 ms
Execution time: 15000 ms
```

**After** (estimated):
```
Hash Join  (cost=20..100 rows=1000)
  ->  Seq Scan on experiments_experiment  (cost=0..50)
  ->  Hash  (cost=20..20 rows=1000)
        ->  Seq Scan on experiments_statistics  (cost=0..20)
Planning time: 1 ms
Execution time: 50 ms
```

## Conclusion

This design provides a scalable, maintainable solution for caching experiment statistics that:

1. **Dramatically improves performance** through pre-computed SQL caches
2. **Maintains accuracy** through scheduled refreshes
3. **Scales efficiently** with data compression and retention policies
4. **Follows Django patterns** and existing codebase conventions
5. **Provides flexibility** for future enhancements (incremental, hybrid strategies)

The phased implementation approach allows for incremental delivery of value while minimizing risk.
