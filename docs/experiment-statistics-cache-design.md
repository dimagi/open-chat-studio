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

## Schema Design: Two Approaches

### Approach Comparison

There are two primary approaches for structuring the cache:

1. **Single-Row Aggregates** (Simpler): One row per experiment/session with total counts
2. **Time-Bucketed Data** (More Powerful): Multiple rows per experiment with time-series data

#### Quick Comparison Table

| Aspect | Single-Row | Time-Bucketed |
|--------|-----------|---------------|
| **Query Simplicity** | ✅ Very simple (single row) | ⚠️ Requires SUM across buckets |
| **Storage Efficiency** | ✅ Minimal storage | ⚠️ More storage (multiple buckets) |
| **Compression** | ⚠️ Manual deletion only | ✅ Natural (merge old buckets) |
| **Trend Analysis** | ❌ No historical data | ✅ Built-in time-series |
| **Incremental Updates** | ⚠️ Need full recalc | ✅ Update current bucket only |
| **Fast Totals** | ✅ Single row lookup | ⚠️ Need to sum (or denormalize) |
| **Data Lifecycle** | ⚠️ Manual management | ✅ Automatic aging |
| **Initial Complexity** | ✅ Simple | ⚠️ More complex |

#### Recommendation

**Hybrid Approach**: Use time-bucketed storage with denormalized totals:

- **Time buckets** for historical tracking, trends, and incremental updates
- **Denormalized totals** (materialized view or separate table) for fast lookups
- Best of both worlds: performance + flexibility

---

## Database Schema

### Option A: Single-Row Aggregates (Original Design)

#### 1. Experiment Statistics Cache

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

---

### Option B: Time-Bucketed Statistics (Recommended)

This approach stores statistics in time buckets, enabling trend analysis, natural compression, and efficient incremental updates.

#### 1. Experiment Statistics Buckets

```python
# apps/experiments/models.py

class ExperimentStatisticsBucket(BaseTeamModel):
    """
    Time-bucketed statistics for experiments.
    Enables historical tracking, trends, and efficient updates.
    """

    class BucketSize(models.TextChoices):
        HOUR = 'hour', 'Hourly'
        DAY = 'day', 'Daily'
        WEEK = 'week', 'Weekly'
        MONTH = 'month', 'Monthly'

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name='statistics_buckets'
    )

    # Time bucket definition
    bucket_size = models.CharField(
        max_length=10,
        choices=BucketSize.choices,
        default=BucketSize.HOUR
    )
    bucket_start = models.DateTimeField(db_index=True)
    bucket_end = models.DateTimeField(db_index=True)

    # Aggregate statistics for this time period
    session_count = models.IntegerField(default=0)
    new_participant_count = models.IntegerField(
        default=0,
        help_text="New participants in this bucket"
    )
    human_message_count = models.IntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    last_updated_at = models.DateTimeField(auto_now=True)
    is_finalized = models.BooleanField(
        default=False,
        help_text="Whether this bucket is complete and won't be updated"
    )

    class Meta:
        db_table = 'experiments_experiment_statistics_bucket'
        unique_together = [('experiment', 'bucket_start', 'bucket_size')]
        indexes = [
            models.Index(fields=['experiment', 'bucket_start', 'bucket_size']),
            models.Index(fields=['bucket_start', 'bucket_end']),
            models.Index(fields=['is_finalized']),
        ]
        ordering = ['-bucket_start']

    def __str__(self):
        return f"{self.experiment.name} - {self.bucket_size} ({self.bucket_start.date()})"

    @classmethod
    def get_bucket_boundaries(cls, dt, bucket_size):
        """Calculate bucket start/end for a given datetime."""
        from django.utils import timezone

        if bucket_size == cls.BucketSize.HOUR:
            start = dt.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1)
        elif bucket_size == cls.BucketSize.DAY:
            start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif bucket_size == cls.BucketSize.WEEK:
            start = dt - timedelta(days=dt.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(weeks=1)
        elif bucket_size == cls.BucketSize.MONTH:
            start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)

        return start, end

    @classmethod
    def get_or_create_bucket(cls, experiment, dt, bucket_size):
        """Get or create a bucket for the given experiment and datetime."""
        start, end = cls.get_bucket_boundaries(dt, bucket_size)

        bucket, created = cls.objects.get_or_create(
            experiment=experiment,
            bucket_size=bucket_size,
            bucket_start=start,
            defaults={
                'bucket_end': end,
                'team': experiment.team,
            }
        )
        return bucket
```

#### 2. Session Statistics Buckets

```python
# apps/experiments/models.py

class SessionStatisticsBucket(BaseTeamModel):
    """
    Time-bucketed statistics for experiment sessions.
    """

    class BucketSize(models.TextChoices):
        HOUR = 'hour', 'Hourly'
        DAY = 'day', 'Daily'

    session = models.ForeignKey(
        ExperimentSession,
        on_delete=models.CASCADE,
        related_name='statistics_buckets'
    )

    # Time bucket
    bucket_size = models.CharField(
        max_length=10,
        choices=BucketSize.choices,
        default=BucketSize.HOUR
    )
    bucket_start = models.DateTimeField(db_index=True)
    bucket_end = models.DateTimeField(db_index=True)

    # Statistics for this period
    human_message_count = models.IntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    last_updated_at = models.DateTimeField(auto_now=True)
    is_finalized = models.BooleanField(default=False)

    class Meta:
        db_table = 'experiments_session_statistics_bucket'
        unique_together = [('session', 'bucket_start', 'bucket_size')]
        indexes = [
            models.Index(fields=['session', 'bucket_start']),
            models.Index(fields=['is_finalized']),
        ]

    @classmethod
    def get_bucket_boundaries(cls, dt, bucket_size):
        """Calculate bucket start/end for a given datetime."""
        if bucket_size == cls.BucketSize.HOUR:
            start = dt.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1)
        elif bucket_size == cls.BucketSize.DAY:
            start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)

        return start, end
```

#### 3. Denormalized Totals (For Fast Access)

```python
# apps/experiments/models.py

class ExperimentStatisticsTotals(BaseTeamModel):
    """
    Denormalized totals for fast access.
    Computed by summing buckets, refreshed periodically.
    """

    experiment = models.OneToOneField(
        Experiment,
        on_delete=models.CASCADE,
        related_name='statistics_totals',
        primary_key=True
    )

    # Aggregate totals (sum of all buckets)
    total_session_count = models.IntegerField(default=0)
    total_participant_count = models.IntegerField(default=0)
    total_human_message_count = models.IntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    last_updated_at = models.DateTimeField(auto_now=True)
    oldest_bucket_start = models.DateTimeField(null=True, blank=True)
    newest_bucket_end = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'experiments_experiment_statistics_totals'

    def refresh_from_buckets(self):
        """Recalculate totals from bucket data."""
        from django.db.models import Sum, Max, Min

        bucket_aggregates = self.experiment.statistics_buckets.aggregate(
            total_sessions=Sum('session_count'),
            total_messages=Sum('human_message_count'),
            last_activity=Max('last_activity_at'),
            oldest_bucket=Min('bucket_start'),
            newest_bucket=Max('bucket_end'),
        )

        # Get unique participant count (needs special handling)
        from apps.experiments.models import ExperimentSession
        participant_count = ExperimentSession.objects.filter(
            experiment=self.experiment
        ).values('participant').distinct().count()

        self.total_session_count = bucket_aggregates['total_sessions'] or 0
        self.total_participant_count = participant_count
        self.total_human_message_count = bucket_aggregates['total_messages'] or 0
        self.last_activity_at = bucket_aggregates['last_activity']
        self.oldest_bucket_start = bucket_aggregates['oldest_bucket']
        self.newest_bucket_end = bucket_aggregates['newest_bucket']
        self.save()

class SessionStatisticsTotals(BaseTeamModel):
    """
    Denormalized session totals for fast access.
    """

    session = models.OneToOneField(
        ExperimentSession,
        on_delete=models.CASCADE,
        related_name='statistics_totals',
        primary_key=True
    )

    total_human_message_count = models.IntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    experiment_versions = models.CharField(max_length=500, blank=True)

    last_updated_at = models.DateTimeField(auto_now=True)
    is_complete = models.BooleanField(default=False)

    class Meta:
        db_table = 'experiments_session_statistics_totals'

    def refresh_from_buckets(self):
        """Recalculate totals from bucket data."""
        from django.db.models import Sum, Max

        bucket_aggregates = self.session.statistics_buckets.aggregate(
            total_messages=Sum('human_message_count'),
            last_activity=Max('last_activity_at'),
        )

        # Get experiment versions (stored separately)
        from django.contrib.contenttypes.models import ContentType
        from apps.annotations.models import CustomTaggedItem
        from apps.chat.models import Chat, ChatMessage

        message_ct = ContentType.objects.get_for_model(ChatMessage)
        versions = CustomTaggedItem.objects.filter(
            content_type=message_ct,
            object_id__in=ChatMessage.objects.filter(
                chat=self.session.chat
            ).values('id'),
            tag__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
        ).values_list('tag__name', flat=True).distinct().order_by('tag__name')

        self.total_human_message_count = bucket_aggregates['total_messages'] or 0
        self.last_activity_at = bucket_aggregates['last_activity']
        self.experiment_versions = ', '.join(versions) if versions else ''
        self.is_complete = self.session.status == SessionStatus.COMPLETE
        self.save()
```

#### 4. Bucket Compression and Lifecycle

```python
# apps/experiments/models.py

class BucketCompressionPolicy(models.Model):
    """
    Defines how buckets are compressed over time.
    E.g., "After 7 days, compress hourly to daily"
    """

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    # Age threshold
    age_days = models.IntegerField(
        help_text="Compress buckets older than this many days"
    )

    # Compression action
    source_bucket_size = models.CharField(
        max_length=10,
        choices=ExperimentStatisticsBucket.BucketSize.choices
    )
    target_bucket_size = models.CharField(
        max_length=10,
        choices=ExperimentStatisticsBucket.BucketSize.choices
    )

    # Whether to delete source buckets after compression
    delete_source = models.BooleanField(default=True)

    # Active status
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'experiments_bucket_compression_policy'
        ordering = ['age_days']

# Default policies could be:
# - 7 days: hour -> day
# - 30 days: day -> week
# - 90 days: week -> month
# - 365 days: delete monthly buckets (keep totals only)
```

#### Time-Bucketed Design Benefits

**1. Natural Incremental Updates**
```python
# When a new message arrives, only update the current hour bucket
current_bucket = ExperimentStatisticsBucket.get_or_create_bucket(
    experiment, timezone.now(), BucketSize.HOUR
)
current_bucket.human_message_count += 1
current_bucket.last_activity_at = timezone.now()
current_bucket.save()
```

**2. Built-in Trend Analysis**
```python
# Get activity for last 7 days
seven_days_ago = timezone.now() - timedelta(days=7)
daily_buckets = ExperimentStatisticsBucket.objects.filter(
    experiment=experiment,
    bucket_size=BucketSize.DAY,
    bucket_start__gte=seven_days_ago
).order_by('bucket_start')

# Can easily plot trends, show sparklines, etc.
```

**3. Automatic Compression**
```python
# Compress old hourly buckets to daily
from django.db.models import Sum, Max

hourly_buckets = ExperimentStatisticsBucket.objects.filter(
    experiment=experiment,
    bucket_size=BucketSize.HOUR,
    bucket_start__date=target_date
)

aggregates = hourly_buckets.aggregate(
    total_sessions=Sum('session_count'),
    total_messages=Sum('human_message_count'),
    last_activity=Max('last_activity_at'),
)

# Create daily bucket
daily_bucket = ExperimentStatisticsBucket.objects.create(
    experiment=experiment,
    bucket_size=BucketSize.DAY,
    bucket_start=target_date.replace(hour=0, minute=0),
    bucket_end=target_date.replace(hour=0, minute=0) + timedelta(days=1),
    session_count=aggregates['total_sessions'],
    human_message_count=aggregates['total_messages'],
    last_activity_at=aggregates['last_activity'],
    is_finalized=True,
)

# Delete hourly buckets
hourly_buckets.delete()
```

**4. Flexible Retention**
```python
# Keep different granularities for different time periods:
# - Last 48 hours: hourly buckets
# - Last 30 days: daily buckets
# - Last 12 months: weekly buckets
# - Older: monthly buckets or delete (keep totals only)
```

---

### Option C: Hybrid (Best of Both Worlds)

Combine time buckets with denormalized totals:

**Schema**:
- `ExperimentStatisticsBucket` - Time-series data
- `ExperimentStatisticsTotals` - Denormalized totals (materialized sum of buckets)
- Automatic refresh of totals from buckets

**Advantages**:
- ✅ Fast total queries (single row lookup)
- ✅ Historical trend data available
- ✅ Efficient incremental updates (update current bucket)
- ✅ Natural compression (merge old buckets)
- ✅ Flexible data lifecycle

**Complexity**: Moderate (two related tables to manage)

**Recommendation**: **This is the best long-term solution** - start with Option A for simplicity, then migrate to Option C when trend analysis is needed.

---

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

### Phased Implementation Approach

**Phase 1: Start Simple (Option A - Single-Row Aggregates)**

Implement the simple single-row cache first:
1. Create `ExperimentStatistics` and `SessionStatistics` tables
2. Implement scheduled full refresh (Strategy 1)
3. Update views to use cached data
4. Measure performance improvements

**Timeline**: 1-2 weeks
**Benefits**:
- ✅ Immediate 10-50x performance improvement
- ✅ Low implementation risk
- ✅ Simple to understand and debug
- ✅ Quick to deploy

**Phase 2: Add Intelligence (If Needed)**

If requirements emerge for trend analysis or better incremental updates:
1. Migrate to time-bucketed design (Option B/C)
2. Backfill historical buckets from existing data
3. Implement bucket compression policies
4. Add trend visualization

**Timeline**: 2-3 weeks
**Benefits**:
- ✅ Historical trend data available
- ✅ More efficient incremental updates
- ✅ Natural data compression
- ✅ Enables time-series analysis

### Decision Matrix

**Choose Option A (Single-Row)** if:
- ✅ You only need current totals (no trends)
- ✅ You want the simplest solution
- ✅ You can tolerate full recalculation
- ✅ Storage optimization isn't critical yet

**Choose Option C (Time-Bucketed + Totals)** if:
- ✅ You need trend analysis / historical data
- ✅ You have very large data volumes (millions of messages)
- ✅ You want efficient incremental updates
- ✅ You need flexible data retention policies
- ✅ You already have requirements for activity charts/graphs

### Final Recommendation

**Start with Option A** because:
1. It solves the immediate performance problem
2. It's the simplest to implement and test
3. It can be migrated to Option C later if needed
4. The performance improvement alone justifies the effort

**Migrate to Option C when**:
- You need to show activity trends over time
- Data volume requires more efficient compression
- You want to add activity charts/sparklines to the UI
- You need more granular activity tracking

This approach provides:
- ✅ Immediate performance improvements (Option A)
- ✅ Simple, maintainable code initially
- ✅ Low risk, incremental delivery
- ✅ Clear migration path to advanced features (Option C)
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

This design document presents a comprehensive solution for caching experiment statistics with two complementary approaches:

### Option A: Single-Row Aggregates
A simple, proven approach that:
- ✅ **Solves the immediate problem** with 10-50x performance improvement
- ✅ **Minimal complexity** - easy to implement, test, and maintain
- ✅ **Quick delivery** - can be implemented in 1-2 weeks
- ✅ **Low risk** - straightforward logic, easy to debug

### Option C: Time-Bucketed + Totals
A sophisticated approach that:
- ✅ **Enables trend analysis** with built-in time-series data
- ✅ **Scales better** with natural compression and efficient incremental updates
- ✅ **Flexible data lifecycle** - automatic aging and retention policies
- ✅ **Future-proof** - supports advanced features like activity charts

### Core Benefits (Both Options)

1. **Dramatic performance improvements** through pre-computed SQL caches
2. **Maintains accuracy** through scheduled and/or incremental refreshes
3. **Scales efficiently** with smart compression and retention policies
4. **Follows Django patterns** and existing codebase conventions
5. **Graceful degradation** when cache is missing or stale
6. **Clear migration path** from simple to advanced

### Recommended Path Forward

1. **Start with Option A** for immediate wins (1-2 weeks)
2. **Measure and validate** performance improvements
3. **Migrate to Option C** when trend analysis or advanced features are needed (2-3 weeks)

This phased approach delivers immediate value while building a foundation for future enhancements, all while minimizing risk and complexity.
