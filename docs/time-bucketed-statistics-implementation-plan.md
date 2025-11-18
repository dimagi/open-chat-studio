# Time-Bucketed Statistics Implementation Plan

## Overview

This document provides a complete implementation plan for caching experiment and session statistics using time-bucketed storage with automatic compression. This approach stores statistics in time buckets (hourly/daily/monthly) that are automatically compressed over time, providing both performance and historical data retention.

## Design Decisions

### Key Simplifications

1. **No Denormalized Totals Tables**: Query totals by summing across buckets (typically 1-30 buckets)
2. **Static Compression Policy**: Hardcoded policy instead of configurable database table
3. **No Deletion**: Compress old buckets into larger ones, never delete
4. **Simple Bucket Structure**: Just experiment and session buckets

### Compression Policy (Static)

```python
# Retention policy (hardcoded in settings or code):
# - 0-24 hours: Hourly buckets
# - 1-30 days: Daily buckets
# - 30+ days: Monthly buckets
# - Never delete, only compress
```

### Trade-offs

**Benefits**:
- ✅ No sync issues between buckets and totals
- ✅ Simpler schema (2 tables instead of 4+)
- ✅ Historical data naturally preserved
- ✅ Easy to understand and debug
- ✅ Flexible querying (can sum any time range)

**Considerations**:
- ⚠️ Totals require SUM() across buckets (typically 1-30 rows)
- ⚠️ Slightly slower than single-row lookup, but still fast enough

---

## Database Schema

### 1. Experiment Statistics Buckets

```python
# apps/experiments/models.py

from datetime import timedelta
from django.db import models
from django.utils import timezone
from apps.teams.models import BaseTeamModel


class ExperimentStatisticsBucket(BaseTeamModel):
    """
    Time-bucketed statistics for experiments.

    Buckets are automatically compressed over time:
    - Recent data (0-24h): hourly buckets
    - Medium-term (1-30 days): daily buckets
    - Long-term (30+ days): monthly buckets
    """

    class BucketSize(models.TextChoices):
        HOUR = 'hour', 'Hourly'
        DAY = 'day', 'Daily'
        MONTH = 'month', 'Monthly'

    experiment = models.ForeignKey(
        'experiments.Experiment',
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
        help_text="New participants that started in this bucket"
    )
    human_message_count = models.IntegerField(default=0)
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last human message in this bucket"
    )

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
            models.Index(fields=['is_finalized', 'bucket_size']),
            models.Index(fields=['experiment', 'is_finalized']),
        ]
        ordering = ['-bucket_start']

    def __str__(self):
        return f"{self.experiment.name} - {self.bucket_size} ({self.bucket_start.date()})"

    @classmethod
    def get_bucket_boundaries(cls, dt, bucket_size):
        """Calculate bucket start/end for a given datetime."""
        if bucket_size == cls.BucketSize.HOUR:
            start = dt.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1)
        elif bucket_size == cls.BucketSize.DAY:
            start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif bucket_size == cls.BucketSize.MONTH:
            start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
        else:
            raise ValueError(f"Invalid bucket_size: {bucket_size}")

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
        return bucket, created


class ExperimentStatisticsBucketManager(models.Manager):
    """Manager with helper methods for querying statistics."""

    def get_totals_for_experiment(self, experiment):
        """
        Get aggregated totals for an experiment across all buckets.
        Returns dict with total_sessions, total_participants, total_messages, last_activity.
        """
        from django.db.models import Sum, Max

        buckets = self.filter(experiment=experiment)

        aggregates = buckets.aggregate(
            total_sessions=Sum('session_count'),
            total_messages=Sum('human_message_count'),
            last_activity=Max('last_activity_at'),
        )

        # Participant count needs special handling (can't sum new_participant_count)
        from apps.experiments.models import ExperimentSession
        participant_count = ExperimentSession.objects.filter(
            experiment=experiment
        ).values('participant').distinct().count()

        return {
            'total_sessions': aggregates['total_sessions'] or 0,
            'total_participants': participant_count,
            'total_messages': aggregates['total_messages'] or 0,
            'last_activity': aggregates['last_activity'],
        }

    def get_totals_for_experiments(self, experiment_ids):
        """
        Get totals for multiple experiments efficiently.
        Returns dict mapping experiment_id -> totals dict.
        """
        from django.db.models import Sum, Max

        # Get bucket aggregates per experiment
        bucket_data = self.filter(
            experiment_id__in=experiment_ids
        ).values('experiment_id').annotate(
            total_sessions=Sum('session_count'),
            total_messages=Sum('human_message_count'),
            last_activity=Max('last_activity_at'),
        )

        # Get participant counts per experiment
        from apps.experiments.models import ExperimentSession
        participant_data = ExperimentSession.objects.filter(
            experiment_id__in=experiment_ids
        ).values('experiment_id').annotate(
            participant_count=models.Count('participant', distinct=True)
        )

        # Combine results
        results = {}
        for bucket in bucket_data:
            results[bucket['experiment_id']] = {
                'total_sessions': bucket['total_sessions'] or 0,
                'total_messages': bucket['total_messages'] or 0,
                'last_activity': bucket['last_activity'],
                'total_participants': 0,  # Will be updated below
            }

        for participant in participant_data:
            exp_id = participant['experiment_id']
            if exp_id in results:
                results[exp_id]['total_participants'] = participant['participant_count']

        return results


# Add the manager to the model
ExperimentStatisticsBucket.add_to_class('objects', ExperimentStatisticsBucketManager())
```

### 2. Session Statistics Buckets

```python
# apps/experiments/models.py

class SessionStatisticsBucket(BaseTeamModel):
    """
    Time-bucketed statistics for experiment sessions.

    Similar compression policy to experiments:
    - Recent: hourly buckets
    - Medium-term: daily buckets
    """

    class BucketSize(models.TextChoices):
        HOUR = 'hour', 'Hourly'
        DAY = 'day', 'Daily'

    session = models.ForeignKey(
        'experiments.ExperimentSession',
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
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last human message in this bucket"
    )

    # Metadata
    last_updated_at = models.DateTimeField(auto_now=True)
    is_finalized = models.BooleanField(default=False)

    class Meta:
        db_table = 'experiments_session_statistics_bucket'
        unique_together = [('session', 'bucket_start', 'bucket_size')]
        indexes = [
            models.Index(fields=['session', 'bucket_start', 'bucket_size']),
            models.Index(fields=['is_finalized', 'bucket_size']),
            models.Index(fields=['session', 'is_finalized']),
        ]
        ordering = ['-bucket_start']

    def __str__(self):
        return f"Session {self.session.external_id} - {self.bucket_size} ({self.bucket_start.date()})"

    @classmethod
    def get_bucket_boundaries(cls, dt, bucket_size):
        """Calculate bucket start/end for a given datetime."""
        if bucket_size == cls.BucketSize.HOUR:
            start = dt.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1)
        elif bucket_size == cls.BucketSize.DAY:
            start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        else:
            raise ValueError(f"Invalid bucket_size: {bucket_size}")

        return start, end

    @classmethod
    def get_or_create_bucket(cls, session, dt, bucket_size):
        """Get or create a bucket for the given session and datetime."""
        start, end = cls.get_bucket_boundaries(dt, bucket_size)

        bucket, created = cls.objects.get_or_create(
            session=session,
            bucket_size=bucket_size,
            bucket_start=start,
            defaults={
                'bucket_end': end,
                'team': session.team,
            }
        )
        return bucket, created


class SessionStatisticsBucketManager(models.Manager):
    """Manager with helper methods for querying session statistics."""

    def get_totals_for_session(self, session):
        """
        Get aggregated totals for a session across all buckets.
        Returns dict with total_messages, last_activity.
        """
        from django.db.models import Sum, Max

        aggregates = self.filter(session=session).aggregate(
            total_messages=Sum('human_message_count'),
            last_activity=Max('last_activity_at'),
        )

        # Get experiment versions (not stored in buckets)
        from django.contrib.contenttypes.models import ContentType
        from apps.annotations.models import CustomTaggedItem
        from apps.chat.models import Chat, ChatMessage

        message_ct = ContentType.objects.get_for_model(ChatMessage)
        versions = CustomTaggedItem.objects.filter(
            content_type=message_ct,
            object_id__in=ChatMessage.objects.filter(
                chat=session.chat
            ).values('id'),
            tag__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
        ).values_list('tag__name', flat=True).distinct().order_by('tag__name')

        return {
            'total_messages': aggregates['total_messages'] or 0,
            'last_activity': aggregates['last_activity'],
            'experiment_versions': ', '.join(versions) if versions else '',
        }

    def get_totals_for_sessions(self, session_ids):
        """
        Get totals for multiple sessions efficiently.
        Returns dict mapping session_id -> totals dict.
        """
        from django.db.models import Sum, Max

        bucket_data = self.filter(
            session_id__in=session_ids
        ).values('session_id').annotate(
            total_messages=Sum('human_message_count'),
            last_activity=Max('last_activity_at'),
        )

        results = {}
        for bucket in bucket_data:
            results[bucket['session_id']] = {
                'total_messages': bucket['total_messages'] or 0,
                'last_activity': bucket['last_activity'],
            }

        return results


SessionStatisticsBucket.add_to_class('objects', SessionStatisticsBucketManager())
```

---

## Compression Policy Configuration

```python
# apps/experiments/statistics_config.py

"""
Static configuration for statistics bucket compression.
"""

from datetime import timedelta
from apps.experiments.models import (
    ExperimentStatisticsBucket,
    SessionStatisticsBucket,
)


class CompressionPolicy:
    """
    Static compression policy for statistics buckets.

    Rules:
    - 0-24 hours: Keep hourly buckets
    - 1-30 days: Compress hourly -> daily
    - 30+ days: Compress daily -> monthly
    """

    # Thresholds (how old data must be before compression)
    HOURLY_TO_DAILY_THRESHOLD = timedelta(days=1)
    DAILY_TO_MONTHLY_THRESHOLD = timedelta(days=30)

    @classmethod
    def should_compress_to_daily(cls, bucket_start):
        """Check if an hourly bucket should be compressed to daily."""
        from django.utils import timezone
        age = timezone.now() - bucket_start
        return age > cls.HOURLY_TO_DAILY_THRESHOLD

    @classmethod
    def should_compress_to_monthly(cls, bucket_start):
        """Check if a daily bucket should be compressed to monthly."""
        from django.utils import timezone
        age = timezone.now() - bucket_start
        return age > cls.DAILY_TO_MONTHLY_THRESHOLD

    @classmethod
    def get_target_bucket_size(cls, current_size, bucket_start):
        """
        Determine the target bucket size for compression.
        Returns None if no compression needed.
        """
        if current_size == ExperimentStatisticsBucket.BucketSize.HOUR:
            if cls.should_compress_to_daily(bucket_start):
                return ExperimentStatisticsBucket.BucketSize.DAY
        elif current_size == ExperimentStatisticsBucket.BucketSize.DAY:
            if cls.should_compress_to_monthly(bucket_start):
                return ExperimentStatisticsBucket.BucketSize.MONTH

        return None


# Configuration for scheduled tasks
STATISTICS_CONFIG = {
    # How often to update buckets (seconds)
    'UPDATE_INTERVAL': 120,  # 2 minutes

    # How often to run compression (seconds)
    'COMPRESSION_INTERVAL': 3600,  # 1 hour

    # Batch size for processing
    'BATCH_SIZE': 100,

    # Whether to finalize old buckets
    'AUTO_FINALIZE': True,

    # Age threshold for auto-finalizing buckets
    'FINALIZE_AGE': timedelta(hours=2),
}
```

---

## Celery Tasks

### 1. Update Current Buckets

```python
# apps/experiments/tasks.py

import logging
from datetime import timedelta
from celery import shared_task
from django.db.models import Count, Q, Max
from django.utils import timezone

from apps.experiments.models import (
    Experiment,
    ExperimentSession,
    ExperimentStatisticsBucket,
    SessionStatisticsBucket,
)
from apps.chat.models import ChatMessage, ChatMessageType

logger = logging.getLogger('ocs.experiments.statistics')


@shared_task
def update_experiment_buckets(experiment_id=None, hours_back=24):
    """
    Update experiment statistics buckets for recent activity.

    Args:
        experiment_id: If provided, update only this experiment
        hours_back: How many hours of data to process (default: 24)
    """
    from apps.experiments.statistics_config import STATISTICS_CONFIG

    cutoff = timezone.now() - timedelta(hours=hours_back)

    # Get experiments with recent activity
    if experiment_id:
        experiments = Experiment.objects.filter(id=experiment_id)
    else:
        experiments = Experiment.objects.filter(
            working_version__isnull=True,
            is_archived=False,
            sessions__chat__messages__created_at__gte=cutoff,
            sessions__chat__messages__message_type=ChatMessageType.HUMAN,
        ).distinct()

    for experiment in experiments.iterator(chunk_size=STATISTICS_CONFIG['BATCH_SIZE']):
        _update_experiment_buckets(experiment, cutoff)


def _update_experiment_buckets(experiment, cutoff):
    """Update buckets for a single experiment."""
    from django.db.models import Count, Max

    # Get all messages for this experiment since cutoff
    messages = ChatMessage.objects.filter(
        chat__experiment_session__experiment=experiment,
        message_type=ChatMessageType.HUMAN,
        created_at__gte=cutoff,
    ).order_by('created_at')

    # Group messages by hour
    hourly_data = {}
    for message in messages:
        hour_start = message.created_at.replace(minute=0, second=0, microsecond=0)

        if hour_start not in hourly_data:
            hourly_data[hour_start] = {
                'message_count': 0,
                'last_activity': message.created_at,
                'sessions': set(),
                'participants': set(),
            }

        hourly_data[hour_start]['message_count'] += 1
        hourly_data[hour_start]['last_activity'] = max(
            hourly_data[hour_start]['last_activity'],
            message.created_at
        )

        session = message.chat.experiment_session
        hourly_data[hour_start]['sessions'].add(session.id)
        if session.participant_id:
            hourly_data[hour_start]['participants'].add(session.participant_id)

    # Update or create hourly buckets
    for hour_start, data in hourly_data.items():
        bucket, created = ExperimentStatisticsBucket.get_or_create_bucket(
            experiment,
            hour_start,
            ExperimentStatisticsBucket.BucketSize.HOUR
        )

        # Recalculate bucket stats from scratch for this hour
        hour_end = hour_start + timedelta(hours=1)

        hour_sessions = ExperimentSession.objects.filter(
            experiment=experiment,
            created_at__gte=hour_start,
            created_at__lt=hour_end,
        )

        bucket.session_count = hour_sessions.count()
        bucket.new_participant_count = hour_sessions.values('participant').distinct().count()
        bucket.human_message_count = data['message_count']
        bucket.last_activity_at = data['last_activity']
        bucket.save()

    logger.info(f"Updated {len(hourly_data)} hourly buckets for experiment {experiment.id}")


@shared_task
def update_session_buckets(session_id=None, hours_back=24):
    """
    Update session statistics buckets for recent activity.

    Args:
        session_id: If provided, update only this session
        hours_back: How many hours of data to process (default: 24)
    """
    from apps.experiments.statistics_config import STATISTICS_CONFIG

    cutoff = timezone.now() - timedelta(hours=hours_back)

    # Get sessions with recent activity
    if session_id:
        sessions = ExperimentSession.objects.filter(id=session_id)
    else:
        # Update sessions that:
        # 1. Have recent messages, OR
        # 2. Are not yet complete (status != COMPLETE)
        sessions = ExperimentSession.objects.filter(
            Q(chat__messages__created_at__gte=cutoff) |
            Q(status__ne='complete')
        ).distinct()

    for session in sessions.iterator(chunk_size=STATISTICS_CONFIG['BATCH_SIZE']):
        _update_session_buckets(session, cutoff)


def _update_session_buckets(session, cutoff):
    """Update buckets for a single session."""
    # Get all human messages for this session since cutoff
    messages = ChatMessage.objects.filter(
        chat=session.chat,
        message_type=ChatMessageType.HUMAN,
        created_at__gte=cutoff,
    ).order_by('created_at')

    # Group messages by hour
    hourly_data = {}
    for message in messages:
        hour_start = message.created_at.replace(minute=0, second=0, microsecond=0)

        if hour_start not in hourly_data:
            hourly_data[hour_start] = {
                'message_count': 0,
                'last_activity': message.created_at,
            }

        hourly_data[hour_start]['message_count'] += 1
        hourly_data[hour_start]['last_activity'] = max(
            hourly_data[hour_start]['last_activity'],
            message.created_at
        )

    # Update or create hourly buckets
    for hour_start, data in hourly_data.items():
        bucket, created = SessionStatisticsBucket.get_or_create_bucket(
            session,
            hour_start,
            SessionStatisticsBucket.BucketSize.HOUR
        )

        bucket.human_message_count = data['message_count']
        bucket.last_activity_at = data['last_activity']
        bucket.save()

    logger.info(f"Updated {len(hourly_data)} hourly buckets for session {session.id}")
```

### 2. Compression Tasks

```python
# apps/experiments/tasks.py (continued)

@shared_task
def compress_experiment_buckets():
    """
    Compress old experiment statistics buckets.
    - Hourly -> Daily (for buckets older than 1 day)
    - Daily -> Monthly (for buckets older than 30 days)
    """
    from apps.experiments.statistics_config import CompressionPolicy
    from django.db.models import Sum, Max

    # Compress hourly to daily
    hourly_buckets = ExperimentStatisticsBucket.objects.filter(
        bucket_size=ExperimentStatisticsBucket.BucketSize.HOUR,
        is_finalized=False,
    )

    days_to_compress = set()
    for bucket in hourly_buckets:
        if CompressionPolicy.should_compress_to_daily(bucket.bucket_start):
            days_to_compress.add((bucket.experiment_id, bucket.bucket_start.date()))

    logger.info(f"Compressing {len(days_to_compress)} days from hourly to daily")

    for experiment_id, date in days_to_compress:
        _compress_hourly_to_daily(experiment_id, date)

    # Compress daily to monthly
    daily_buckets = ExperimentStatisticsBucket.objects.filter(
        bucket_size=ExperimentStatisticsBucket.BucketSize.DAY,
        is_finalized=False,
    )

    months_to_compress = set()
    for bucket in daily_buckets:
        if CompressionPolicy.should_compress_to_monthly(bucket.bucket_start):
            month_start = bucket.bucket_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            months_to_compress.add((bucket.experiment_id, month_start))

    logger.info(f"Compressing {len(months_to_compress)} months from daily to monthly")

    for experiment_id, month_start in months_to_compress:
        _compress_daily_to_monthly(experiment_id, month_start)


def _compress_hourly_to_daily(experiment_id, date):
    """Compress all hourly buckets for a given day into a single daily bucket."""
    from django.db import transaction
    from django.db.models import Sum, Max

    day_start = timezone.make_aware(datetime.combine(date, datetime.min.time()))
    day_end = day_start + timedelta(days=1)

    # Get all hourly buckets for this day
    hourly_buckets = ExperimentStatisticsBucket.objects.filter(
        experiment_id=experiment_id,
        bucket_size=ExperimentStatisticsBucket.BucketSize.HOUR,
        bucket_start__gte=day_start,
        bucket_start__lt=day_end,
    )

    if not hourly_buckets.exists():
        return

    # Aggregate statistics
    aggregates = hourly_buckets.aggregate(
        total_sessions=Sum('session_count'),
        total_participants=Sum('new_participant_count'),
        total_messages=Sum('human_message_count'),
        last_activity=Max('last_activity_at'),
    )

    with transaction.atomic():
        # Create or update daily bucket
        experiment = Experiment.objects.get(id=experiment_id)
        daily_bucket, created = ExperimentStatisticsBucket.objects.update_or_create(
            experiment=experiment,
            bucket_size=ExperimentStatisticsBucket.BucketSize.DAY,
            bucket_start=day_start,
            defaults={
                'bucket_end': day_end,
                'session_count': aggregates['total_sessions'] or 0,
                'new_participant_count': aggregates['total_participants'] or 0,
                'human_message_count': aggregates['total_messages'] or 0,
                'last_activity_at': aggregates['last_activity'],
                'is_finalized': True,
                'team_id': experiment.team_id,
            }
        )

        # Delete hourly buckets
        count = hourly_buckets.delete()[0]
        logger.info(
            f"Compressed {count} hourly buckets to daily bucket "
            f"for experiment {experiment_id} on {date}"
        )


def _compress_daily_to_monthly(experiment_id, month_start):
    """Compress all daily buckets for a given month into a single monthly bucket."""
    from django.db import transaction
    from django.db.models import Sum, Max

    # Calculate month end
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    # Get all daily buckets for this month
    daily_buckets = ExperimentStatisticsBucket.objects.filter(
        experiment_id=experiment_id,
        bucket_size=ExperimentStatisticsBucket.BucketSize.DAY,
        bucket_start__gte=month_start,
        bucket_start__lt=month_end,
    )

    if not daily_buckets.exists():
        return

    # Aggregate statistics
    aggregates = daily_buckets.aggregate(
        total_sessions=Sum('session_count'),
        total_participants=Sum('new_participant_count'),
        total_messages=Sum('human_message_count'),
        last_activity=Max('last_activity_at'),
    )

    with transaction.atomic():
        # Create or update monthly bucket
        experiment = Experiment.objects.get(id=experiment_id)
        monthly_bucket, created = ExperimentStatisticsBucket.objects.update_or_create(
            experiment=experiment,
            bucket_size=ExperimentStatisticsBucket.BucketSize.MONTH,
            bucket_start=month_start,
            defaults={
                'bucket_end': month_end,
                'session_count': aggregates['total_sessions'] or 0,
                'new_participant_count': aggregates['total_participants'] or 0,
                'human_message_count': aggregates['total_messages'] or 0,
                'last_activity_at': aggregates['last_activity'],
                'is_finalized': True,
                'team_id': experiment.team_id,
            }
        )

        # Delete daily buckets
        count = daily_buckets.delete()[0]
        logger.info(
            f"Compressed {count} daily buckets to monthly bucket "
            f"for experiment {experiment_id} starting {month_start.date()}"
        )


@shared_task
def compress_session_buckets():
    """
    Compress old session statistics buckets.
    - Hourly -> Daily (for buckets older than 1 day)
    """
    from apps.experiments.statistics_config import CompressionPolicy

    # Compress hourly to daily
    hourly_buckets = SessionStatisticsBucket.objects.filter(
        bucket_size=SessionStatisticsBucket.BucketSize.HOUR,
        is_finalized=False,
    )

    days_to_compress = set()
    for bucket in hourly_buckets:
        if CompressionPolicy.should_compress_to_daily(bucket.bucket_start):
            days_to_compress.add((bucket.session_id, bucket.bucket_start.date()))

    logger.info(f"Compressing {len(days_to_compress)} session days from hourly to daily")

    for session_id, date in days_to_compress:
        _compress_session_hourly_to_daily(session_id, date)


def _compress_session_hourly_to_daily(session_id, date):
    """Compress all hourly session buckets for a given day into a single daily bucket."""
    from django.db import transaction
    from django.db.models import Sum, Max
    from datetime import datetime

    day_start = timezone.make_aware(datetime.combine(date, datetime.min.time()))
    day_end = day_start + timedelta(days=1)

    # Get all hourly buckets for this day
    hourly_buckets = SessionStatisticsBucket.objects.filter(
        session_id=session_id,
        bucket_size=SessionStatisticsBucket.BucketSize.HOUR,
        bucket_start__gte=day_start,
        bucket_start__lt=day_end,
    )

    if not hourly_buckets.exists():
        return

    # Aggregate statistics
    aggregates = hourly_buckets.aggregate(
        total_messages=Sum('human_message_count'),
        last_activity=Max('last_activity_at'),
    )

    with transaction.atomic():
        # Create or update daily bucket
        session = ExperimentSession.objects.get(id=session_id)
        daily_bucket, created = SessionStatisticsBucket.objects.update_or_create(
            session=session,
            bucket_size=SessionStatisticsBucket.BucketSize.DAY,
            bucket_start=day_start,
            defaults={
                'bucket_end': day_end,
                'human_message_count': aggregates['total_messages'] or 0,
                'last_activity_at': aggregates['last_activity'],
                'is_finalized': True,
                'team_id': session.team_id,
            }
        )

        # Delete hourly buckets
        count = hourly_buckets.delete()[0]
        logger.info(
            f"Compressed {count} hourly session buckets to daily "
            f"for session {session_id} on {date}"
        )
```

### 3. Backfill Task

```python
# apps/experiments/tasks.py (continued)

@shared_task
def backfill_statistics(experiment_id=None, session_id=None, start_date=None):
    """
    Backfill statistics buckets from existing data.

    Args:
        experiment_id: Backfill specific experiment
        session_id: Backfill specific session
        start_date: Start date for backfill (default: beginning of data)
    """
    if experiment_id:
        _backfill_experiment(experiment_id, start_date)
    elif session_id:
        _backfill_session(session_id, start_date)
    else:
        # Backfill all
        experiments = Experiment.objects.filter(
            working_version__isnull=True,
            is_archived=False,
        )
        for exp in experiments.iterator(chunk_size=50):
            _backfill_experiment(exp.id, start_date)


def _backfill_experiment(experiment_id, start_date=None):
    """Backfill statistics for a single experiment."""
    from django.db.models import Min

    experiment = Experiment.objects.get(id=experiment_id)

    # Determine date range
    if start_date is None:
        first_message = ChatMessage.objects.filter(
            chat__experiment_session__experiment=experiment,
            message_type=ChatMessageType.HUMAN,
        ).aggregate(first=Min('created_at'))['first']

        if not first_message:
            logger.info(f"No messages found for experiment {experiment_id}")
            return

        start_date = first_message.date()

    # Delete existing buckets to avoid duplicates
    ExperimentStatisticsBucket.objects.filter(experiment=experiment).delete()

    # Process in chunks by month to avoid memory issues
    current_date = start_date
    end_date = timezone.now().date()

    while current_date <= end_date:
        month_end = min(
            (current_date.replace(day=1) + timedelta(days=32)).replace(day=1),
            end_date
        )

        logger.info(
            f"Backfilling experiment {experiment_id} from {current_date} to {month_end}"
        )

        # Update buckets for this month
        _update_experiment_buckets(
            experiment,
            cutoff=timezone.make_aware(
                datetime.combine(current_date, datetime.min.time())
            )
        )

        current_date = month_end

    logger.info(f"Completed backfill for experiment {experiment_id}")


def _backfill_session(session_id, start_date=None):
    """Backfill statistics for a single session."""
    from django.db.models import Min

    session = ExperimentSession.objects.get(id=session_id)

    # Determine date range
    if start_date is None:
        first_message = ChatMessage.objects.filter(
            chat=session.chat,
            message_type=ChatMessageType.HUMAN,
        ).aggregate(first=Min('created_at'))['first']

        if not first_message:
            return

        start_date = first_message.date()

    # Delete existing buckets
    SessionStatisticsBucket.objects.filter(session=session).delete()

    # Update buckets
    _update_session_buckets(
        session,
        cutoff=timezone.make_aware(
            datetime.combine(start_date, datetime.min.time())
        )
    )

    logger.info(f"Completed backfill for session {session_id}")
```

---

## Scheduled Tasks Configuration

```python
# config/settings.py

# Add to SCHEDULED_TASKS dictionary

SCHEDULED_TASKS = {
    # ... existing tasks ...

    "experiments.tasks.update_experiment_buckets": {
        "task": "apps.experiments.tasks.update_experiment_buckets",
        "schedule": 120,  # Every 2 minutes
    },
    "experiments.tasks.update_session_buckets": {
        "task": "apps.experiments.tasks.update_session_buckets",
        "schedule": 120,  # Every 2 minutes
    },
    "experiments.tasks.compress_experiment_buckets": {
        "task": "apps.experiments.tasks.compress_experiment_buckets",
        "schedule": 3600,  # Every hour
    },
    "experiments.tasks.compress_session_buckets": {
        "task": "apps.experiments.tasks.compress_session_buckets",
        "schedule": 3600,  # Every hour
    },
}
```

---

## View Integration

### Update ChatbotExperimentTableView

```python
# apps/chatbots/views.py

class ChatbotExperimentTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    template_name = "table/single_table.html"
    model = Experiment
    table_class = ChatbotTable
    permission_required = "experiments.view_experiment"

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        if not flag_is_active(self.request, "flag_tracing"):
            table.exclude = ("trends",)
        return table

    def get_queryset(self):
        """Returns a lightweight queryset for counting."""
        query_set = (
            self.model.objects.get_all()
            .filter(team=self.request.team, working_version__isnull=True, pipeline__isnull=False)
            .select_related("team", "owner")
        )
        show_archived = self.request.GET.get("show_archived") == "on"
        if not show_archived:
            query_set = query_set.filter(is_archived=False)

        search = self.request.GET.get("search")
        if search:
            query_set = similarity_search(
                query_set,
                search_phase=search,
                columns=["name", "description"],
                extra_conditions=Q(owner__username__icontains=search),
                score=0.1,
            )
        return query_set

    def get_table_data(self):
        """Add statistics from buckets."""
        queryset = super().get_table_data()

        # Get all experiment IDs in this page
        experiment_ids = list(queryset.values_list('id', flat=True))

        # Fetch statistics for all experiments in batch
        from apps.experiments.models import ExperimentStatisticsBucket
        stats_map = ExperimentStatisticsBucket.objects.get_totals_for_experiments(experiment_ids)

        # Annotate queryset with statistics
        experiments_with_stats = []
        for experiment in queryset:
            stats = stats_map.get(experiment.id, {
                'total_sessions': 0,
                'total_participants': 0,
                'total_messages': 0,
                'last_activity': None,
            })

            # Add as properties for template access
            experiment.session_count = stats['total_sessions']
            experiment.participant_count = stats['total_participants']
            experiment.messages_count = stats['total_messages']
            experiment.last_message = stats['last_activity']

            experiments_with_stats.append(experiment)

        # Sort by last activity
        experiments_with_stats.sort(
            key=lambda e: e.last_message if e.last_message else timezone.datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )

        return experiments_with_stats
```

### Update ChatbotSessionsTableView

```python
# apps/chatbots/views.py

class ChatbotSessionsTableView(ExperimentSessionsTableView):
    table_class = ChatbotSessionsTable

    def get_table_data(self):
        """Add statistics from buckets."""
        queryset = super().get_table_data()

        # Get session IDs
        session_ids = list(queryset.values_list('id', flat=True))

        # Fetch statistics for all sessions in batch
        from apps.experiments.models import SessionStatisticsBucket
        stats_map = SessionStatisticsBucket.objects.get_totals_for_sessions(session_ids)

        # Annotate queryset
        sessions_with_stats = []
        for session in queryset:
            stats = stats_map.get(session.id, {
                'total_messages': 0,
                'last_activity': None,
            })

            session.message_count = stats['total_messages']
            session.last_message_created_at = stats['last_activity']

            sessions_with_stats.append(session)

        return sessions_with_stats

    def get_table(self, **kwargs):
        """When viewing sessions for a specific chatbot, hide the chatbot column."""
        table = super().get_table(**kwargs)
        if self.kwargs.get("experiment_id"):
            table.exclude = ("chatbot",)
        return table
```

---

## Management Commands

### 1. Refresh Statistics

```python
# apps/experiments/management/commands/refresh_statistics.py

from django.core.management.base import BaseCommand
from apps.experiments.tasks import (
    update_experiment_buckets,
    update_session_buckets,
    compress_experiment_buckets,
    compress_session_buckets,
)


class Command(BaseCommand):
    help = 'Refresh experiment and session statistics buckets'

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
            '--hours-back',
            type=int,
            default=24,
            help='How many hours of data to process (default: 24)'
        )
        parser.add_argument(
            '--compress',
            action='store_true',
            help='Also run compression'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='use_async',
            help='Run tasks asynchronously via Celery'
        )

    def handle(self, *args, **options):
        experiment_id = options.get('experiment_id')
        session_id = options.get('session_id')
        hours_back = options['hours_back']
        use_async = options['use_async']

        if experiment_id:
            self.stdout.write(f"Refreshing experiment {experiment_id}...")
            if use_async:
                update_experiment_buckets.delay(experiment_id, hours_back)
                self.stdout.write(self.style.SUCCESS("Task queued"))
            else:
                update_experiment_buckets(experiment_id, hours_back)
                self.stdout.write(self.style.SUCCESS("Done"))

        elif session_id:
            self.stdout.write(f"Refreshing session {session_id}...")
            if use_async:
                update_session_buckets.delay(session_id, hours_back)
                self.stdout.write(self.style.SUCCESS("Task queued"))
            else:
                update_session_buckets(session_id, hours_back)
                self.stdout.write(self.style.SUCCESS("Done"))

        else:
            self.stdout.write("Refreshing all statistics...")
            if use_async:
                update_experiment_buckets.delay(hours_back=hours_back)
                update_session_buckets.delay(hours_back=hours_back)
                self.stdout.write(self.style.SUCCESS("Tasks queued"))
            else:
                update_experiment_buckets(hours_back=hours_back)
                update_session_buckets(hours_back=hours_back)
                self.stdout.write(self.style.SUCCESS("Done"))

        if options['compress']:
            self.stdout.write("Running compression...")
            if use_async:
                compress_experiment_buckets.delay()
                compress_session_buckets.delay()
                self.stdout.write(self.style.SUCCESS("Compression tasks queued"))
            else:
                compress_experiment_buckets()
                compress_session_buckets()
                self.stdout.write(self.style.SUCCESS("Compression done"))
```

### 2. Backfill Statistics

```python
# apps/experiments/management/commands/backfill_statistics.py

from datetime import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.experiments.tasks import backfill_statistics


class Command(BaseCommand):
    help = 'Backfill statistics buckets from existing data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--experiment-id',
            type=int,
            help='Backfill specific experiment'
        )
        parser.add_argument(
            '--session-id',
            type=int,
            help='Backfill specific session'
        )
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for backfill (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='use_async',
            help='Run task asynchronously via Celery'
        )

    def handle(self, *args, **options):
        experiment_id = options.get('experiment_id')
        session_id = options.get('session_id')
        start_date_str = options.get('start_date')
        use_async = options['use_async']

        start_date = None
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()

        if use_async:
            backfill_statistics.delay(
                experiment_id=experiment_id,
                session_id=session_id,
                start_date=start_date
            )
            self.stdout.write(self.style.SUCCESS("Backfill task queued"))
        else:
            self.stdout.write("Starting backfill...")
            backfill_statistics(
                experiment_id=experiment_id,
                session_id=session_id,
                start_date=start_date
            )
            self.stdout.write(self.style.SUCCESS("Backfill complete"))
```

### 3. Show Statistics

```python
# apps/experiments/management/commands/show_statistics.py

from django.core.management.base import BaseCommand
from apps.experiments.models import (
    Experiment,
    ExperimentSession,
    ExperimentStatisticsBucket,
    SessionStatisticsBucket,
)


class Command(BaseCommand):
    help = 'Show statistics for an experiment or session'

    def add_arguments(self, parser):
        parser.add_argument(
            '--experiment-id',
            type=int,
            help='Show statistics for specific experiment'
        )
        parser.add_argument(
            '--session-id',
            type=int,
            help='Show statistics for specific session'
        )
        parser.add_argument(
            '--buckets',
            action='store_true',
            help='Show individual bucket details'
        )

    def handle(self, *args, **options):
        experiment_id = options.get('experiment_id')
        session_id = options.get('session_id')
        show_buckets = options['buckets']

        if experiment_id:
            self._show_experiment_stats(experiment_id, show_buckets)
        elif session_id:
            self._show_session_stats(session_id, show_buckets)
        else:
            self.stdout.write(self.style.ERROR("Provide --experiment-id or --session-id"))

    def _show_experiment_stats(self, experiment_id, show_buckets):
        try:
            experiment = Experiment.objects.get(id=experiment_id)
        except Experiment.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Experiment {experiment_id} not found"))
            return

        self.stdout.write(self.style.SUCCESS(f"\nExperiment: {experiment.name}"))
        self.stdout.write("-" * 60)

        # Get totals
        totals = ExperimentStatisticsBucket.objects.get_totals_for_experiment(experiment)

        self.stdout.write(f"Total Sessions:     {totals['total_sessions']}")
        self.stdout.write(f"Total Participants: {totals['total_participants']}")
        self.stdout.write(f"Total Messages:     {totals['total_messages']}")
        self.stdout.write(f"Last Activity:      {totals['last_activity']}")

        if show_buckets:
            self.stdout.write(f"\nBuckets:")
            buckets = ExperimentStatisticsBucket.objects.filter(
                experiment=experiment
            ).order_by('-bucket_start')

            for bucket in buckets:
                self.stdout.write(
                    f"  {bucket.bucket_size:6s} | {bucket.bucket_start} | "
                    f"Sessions: {bucket.session_count:4d} | "
                    f"Messages: {bucket.human_message_count:5d} | "
                    f"{'[finalized]' if bucket.is_finalized else ''}"
                )

    def _show_session_stats(self, session_id, show_buckets):
        try:
            session = ExperimentSession.objects.get(id=session_id)
        except ExperimentSession.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Session {session_id} not found"))
            return

        self.stdout.write(self.style.SUCCESS(f"\nSession: {session.external_id}"))
        self.stdout.write("-" * 60)

        # Get totals
        totals = SessionStatisticsBucket.objects.get_totals_for_session(session)

        self.stdout.write(f"Total Messages:   {totals['total_messages']}")
        self.stdout.write(f"Last Activity:    {totals['last_activity']}")
        self.stdout.write(f"Versions:         {totals['experiment_versions']}")

        if show_buckets:
            self.stdout.write(f"\nBuckets:")
            buckets = SessionStatisticsBucket.objects.filter(
                session=session
            ).order_by('-bucket_start')

            for bucket in buckets:
                self.stdout.write(
                    f"  {bucket.bucket_size:6s} | {bucket.bucket_start} | "
                    f"Messages: {bucket.human_message_count:5d} | "
                    f"{'[finalized]' if bucket.is_finalized else ''}"
                )
```

---

## Migration Plan

### Phase 1: Foundation (Week 1)

**Tasks**:
1. Create models (`ExperimentStatisticsBucket`, `SessionStatisticsBucket`)
2. Create migration files
3. Add `statistics_config.py` with compression policy
4. Create management commands

**Deliverables**:
- Django migrations
- Empty tables ready for data
- Management commands for manual control

**Testing**:
```bash
# Create migrations
python manage.py makemigrations experiments

# Apply migrations
python manage.py migrate

# Verify tables exist
python manage.py dbshell
\dt experiments_*_bucket
```

### Phase 2: Backfill (Week 1-2)

**Tasks**:
1. Implement backfill task
2. Test on staging environment
3. Run backfill for production data (in batches)

**Deliverables**:
- Historical data loaded into buckets
- Verified accuracy against live queries

**Testing**:
```bash
# Backfill single experiment (test)
python manage.py backfill_statistics --experiment-id=1

# Verify results
python manage.py show_statistics --experiment-id=1 --buckets

# Backfill all (production)
python manage.py backfill_statistics --async
```

### Phase 3: Scheduled Updates (Week 2)

**Tasks**:
1. Implement update tasks (`update_experiment_buckets`, `update_session_buckets`)
2. Add to SCHEDULED_TASKS
3. Monitor task execution

**Deliverables**:
- Automated bucket updates every 2 minutes
- Monitoring/logging in place

**Testing**:
```bash
# Run manually first
python manage.py refresh_statistics --hours-back=1

# Enable scheduled tasks
python manage.py setup_periodic_tasks

# Monitor Celery logs
inv celery
```

### Phase 4: Compression (Week 2)

**Tasks**:
1. Implement compression tasks
2. Test compression on old data
3. Add to SCHEDULED_TASKS (hourly)

**Deliverables**:
- Automatic compression of old buckets
- Reduced storage footprint

**Testing**:
```bash
# Run compression manually
python manage.py refresh_statistics --compress

# Verify compression
python manage.py show_statistics --experiment-id=1 --buckets
# Should see daily/monthly buckets for old data
```

### Phase 5: View Integration (Week 3)

**Tasks**:
1. Update `ChatbotExperimentTableView` to use buckets
2. Update `ChatbotSessionsTableView` to use buckets
3. Add fallback logic for missing cache
4. Performance testing

**Deliverables**:
- Views using cached statistics
- Fast page loads (< 2 seconds)
- Graceful degradation

**Testing**:
```bash
# Load test page
# Measure query count and time
# Compare before/after performance
```

### Phase 6: Monitoring & Optimization (Week 3-4)

**Tasks**:
1. Add monitoring for cache freshness
2. Tune batch sizes and intervals
3. Add admin interface for cache management
4. Document for team

**Deliverables**:
- Production-ready system
- Monitoring dashboards
- Team documentation

---

## Testing Strategy

### Unit Tests

```python
# apps/experiments/tests/test_statistics_buckets.py

import pytest
from datetime import datetime, timedelta
from django.utils import timezone
from apps.experiments.models import (
    ExperimentStatisticsBucket,
    SessionStatisticsBucket,
)
from apps.utils.factories.experiments import (
    ExperimentFactory,
    ExperimentSessionFactory,
)
from apps.utils.factories.chat import ChatMessageFactory
from apps.chat.models import ChatMessageType


@pytest.mark.django_db
class TestExperimentStatisticsBucket:
    def test_get_bucket_boundaries_hour(self):
        """Test hourly bucket boundary calculation."""
        dt = datetime(2024, 1, 15, 14, 35, 22, tzinfo=timezone.utc)
        start, end = ExperimentStatisticsBucket.get_bucket_boundaries(
            dt,
            ExperimentStatisticsBucket.BucketSize.HOUR
        )

        assert start == datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2024, 1, 15, 15, 0, 0, tzinfo=timezone.utc)

    def test_get_bucket_boundaries_day(self):
        """Test daily bucket boundary calculation."""
        dt = datetime(2024, 1, 15, 14, 35, 22, tzinfo=timezone.utc)
        start, end = ExperimentStatisticsBucket.get_bucket_boundaries(
            dt,
            ExperimentStatisticsBucket.BucketSize.DAY
        )

        assert start == datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc)

    def test_get_bucket_boundaries_month(self):
        """Test monthly bucket boundary calculation."""
        dt = datetime(2024, 1, 15, 14, 35, 22, tzinfo=timezone.utc)
        start, end = ExperimentStatisticsBucket.get_bucket_boundaries(
            dt,
            ExperimentStatisticsBucket.BucketSize.MONTH
        )

        assert start == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2024, 2, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_get_or_create_bucket(self):
        """Test bucket creation."""
        experiment = ExperimentFactory()
        dt = timezone.now()

        bucket, created = ExperimentStatisticsBucket.get_or_create_bucket(
            experiment,
            dt,
            ExperimentStatisticsBucket.BucketSize.HOUR
        )

        assert created is True
        assert bucket.experiment == experiment
        assert bucket.bucket_size == ExperimentStatisticsBucket.BucketSize.HOUR

        # Second call should return existing bucket
        bucket2, created2 = ExperimentStatisticsBucket.get_or_create_bucket(
            experiment,
            dt,
            ExperimentStatisticsBucket.BucketSize.HOUR
        )

        assert created2 is False
        assert bucket2.id == bucket.id

    def test_get_totals_for_experiment(self):
        """Test aggregating totals across buckets."""
        experiment = ExperimentFactory()

        # Create some buckets with data
        now = timezone.now()
        for i in range(3):
            hour = now - timedelta(hours=i)
            bucket, _ = ExperimentStatisticsBucket.get_or_create_bucket(
                experiment,
                hour,
                ExperimentStatisticsBucket.BucketSize.HOUR
            )
            bucket.session_count = 5
            bucket.human_message_count = 10 * (i + 1)
            bucket.last_activity_at = hour
            bucket.save()

        # Get totals
        totals = ExperimentStatisticsBucket.objects.get_totals_for_experiment(experiment)

        assert totals['total_sessions'] == 15  # 5 + 5 + 5
        assert totals['total_messages'] == 60  # 10 + 20 + 30
        assert totals['last_activity'] is not None


@pytest.mark.django_db
class TestCompressionPolicy:
    def test_should_compress_to_daily(self):
        """Test hourly -> daily compression threshold."""
        from apps.experiments.statistics_config import CompressionPolicy

        # Recent bucket (< 1 day old) should not compress
        recent = timezone.now() - timedelta(hours=12)
        assert CompressionPolicy.should_compress_to_daily(recent) is False

        # Old bucket (> 1 day old) should compress
        old = timezone.now() - timedelta(days=2)
        assert CompressionPolicy.should_compress_to_daily(old) is True

    def test_should_compress_to_monthly(self):
        """Test daily -> monthly compression threshold."""
        from apps.experiments.statistics_config import CompressionPolicy

        # Recent bucket (< 30 days old) should not compress
        recent = timezone.now() - timedelta(days=15)
        assert CompressionPolicy.should_compress_to_monthly(recent) is False

        # Old bucket (> 30 days old) should compress
        old = timezone.now() - timedelta(days=35)
        assert CompressionPolicy.should_compress_to_monthly(old) is True


@pytest.mark.django_db
class TestCompressionTasks:
    def test_compress_hourly_to_daily(self):
        """Test compressing multiple hourly buckets into daily."""
        from apps.experiments.tasks import _compress_hourly_to_daily

        experiment = ExperimentFactory()
        target_date = (timezone.now() - timedelta(days=2)).date()

        # Create 24 hourly buckets for the target date
        for hour in range(24):
            dt = timezone.make_aware(
                datetime.combine(target_date, datetime.min.time())
            ) + timedelta(hours=hour)

            bucket, _ = ExperimentStatisticsBucket.get_or_create_bucket(
                experiment,
                dt,
                ExperimentStatisticsBucket.BucketSize.HOUR
            )
            bucket.session_count = 1
            bucket.human_message_count = 10
            bucket.last_activity_at = dt
            bucket.save()

        # Verify 24 hourly buckets exist
        hourly_count = ExperimentStatisticsBucket.objects.filter(
            experiment=experiment,
            bucket_size=ExperimentStatisticsBucket.BucketSize.HOUR
        ).count()
        assert hourly_count == 24

        # Compress
        _compress_hourly_to_daily(experiment.id, target_date)

        # Verify compression
        hourly_count = ExperimentStatisticsBucket.objects.filter(
            experiment=experiment,
            bucket_size=ExperimentStatisticsBucket.BucketSize.HOUR
        ).count()
        assert hourly_count == 0

        daily_buckets = ExperimentStatisticsBucket.objects.filter(
            experiment=experiment,
            bucket_size=ExperimentStatisticsBucket.BucketSize.DAY
        )
        assert daily_buckets.count() == 1

        daily_bucket = daily_buckets.first()
        assert daily_bucket.session_count == 24
        assert daily_bucket.human_message_count == 240
        assert daily_bucket.is_finalized is True
```

### Integration Tests

```python
# apps/experiments/tests/test_statistics_integration.py

@pytest.mark.django_db
class TestStatisticsIntegration:
    def test_end_to_end_workflow(self):
        """Test complete workflow from messages to cached statistics."""
        from apps.experiments.tasks import (
            update_experiment_buckets,
            compress_experiment_buckets,
        )

        # Create experiment with sessions and messages
        experiment = ExperimentFactory()
        session1 = ExperimentSessionFactory(experiment=experiment)
        session2 = ExperimentSessionFactory(experiment=experiment)

        # Create messages at different times
        now = timezone.now()
        for i in range(5):
            ChatMessageFactory(
                chat=session1.chat,
                message_type=ChatMessageType.HUMAN,
                created_at=now - timedelta(hours=i)
            )

        for i in range(3):
            ChatMessageFactory(
                chat=session2.chat,
                message_type=ChatMessageType.HUMAN,
                created_at=now - timedelta(hours=i)
            )

        # Update buckets
        update_experiment_buckets(experiment.id, hours_back=24)

        # Verify buckets created
        buckets = ExperimentStatisticsBucket.objects.filter(experiment=experiment)
        assert buckets.exists()

        # Get totals
        totals = ExperimentStatisticsBucket.objects.get_totals_for_experiment(experiment)
        assert totals['total_messages'] == 8
        assert totals['total_sessions'] == 2
```

### Performance Tests

```python
# apps/experiments/tests/test_statistics_performance.py

@pytest.mark.django_db
class TestStatisticsPerformance:
    def test_query_performance_with_buckets(self):
        """Verify bucket-based queries are fast."""
        from django.test.utils import override_settings
        from django.db import connection
        from django.test import TestCase

        # Create 100 experiments with data
        experiments = []
        for i in range(100):
            exp = ExperimentFactory()
            experiments.append(exp)

            # Create some buckets
            for j in range(30):  # 30 days of daily buckets
                dt = timezone.now() - timedelta(days=j)
                bucket, _ = ExperimentStatisticsBucket.get_or_create_bucket(
                    exp,
                    dt,
                    ExperimentStatisticsBucket.BucketSize.DAY
                )
                bucket.session_count = 5
                bucket.human_message_count = 50
                bucket.save()

        # Measure query performance
        experiment_ids = [e.id for e in experiments]

        # Reset query counter
        from django.db import reset_queries
        reset_queries()

        with override_settings(DEBUG=True):
            # Get totals for all experiments
            stats_map = ExperimentStatisticsBucket.objects.get_totals_for_experiments(
                experiment_ids
            )

            # Should be fast (< 10 queries)
            query_count = len(connection.queries)
            assert query_count < 10

            # Verify results
            assert len(stats_map) == 100
            for exp_id, stats in stats_map.items():
                assert stats['total_sessions'] == 150  # 5 * 30
                assert stats['total_messages'] == 1500  # 50 * 30
```

---

## Monitoring & Observability

### Metrics to Track

```python
# apps/experiments/monitoring.py

import logging
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('ocs.experiments.statistics')


def log_bucket_update(bucket_type, count, duration):
    """Log bucket update performance."""
    logger.info(
        f"Updated {count} {bucket_type} buckets in {duration:.2f}s",
        extra={
            'bucket_type': bucket_type,
            'count': count,
            'duration_seconds': duration,
        }
    )


def log_compression(bucket_type, source_count, target_count, duration):
    """Log compression performance."""
    logger.info(
        f"Compressed {source_count} {bucket_type} buckets to {target_count} in {duration:.2f}s",
        extra={
            'bucket_type': bucket_type,
            'source_count': source_count,
            'target_count': target_count,
            'duration_seconds': duration,
            'compression_ratio': source_count / max(target_count, 1),
        }
    )


def get_cache_health():
    """Get health metrics for statistics cache."""
    from apps.experiments.models import (
        ExperimentStatisticsBucket,
        SessionStatisticsBucket,
    )

    now = timezone.now()
    one_hour_ago = now - timedelta(hours=1)

    # Count recent bucket updates
    recent_experiment_updates = ExperimentStatisticsBucket.objects.filter(
        last_updated_at__gte=one_hour_ago
    ).count()

    recent_session_updates = SessionStatisticsBucket.objects.filter(
        last_updated_at__gte=one_hour_ago
    ).count()

    # Count buckets by size
    exp_bucket_counts = {}
    for size in ExperimentStatisticsBucket.BucketSize:
        exp_bucket_counts[size] = ExperimentStatisticsBucket.objects.filter(
            bucket_size=size
        ).count()

    return {
        'recent_experiment_updates': recent_experiment_updates,
        'recent_session_updates': recent_session_updates,
        'experiment_buckets_by_size': exp_bucket_counts,
        'timestamp': now,
    }
```

### Admin Interface

```python
# apps/experiments/admin.py

from django.contrib import admin
from apps.experiments.models import (
    ExperimentStatisticsBucket,
    SessionStatisticsBucket,
)


@admin.register(ExperimentStatisticsBucket)
class ExperimentStatisticsBucketAdmin(admin.ModelAdmin):
    list_display = [
        'experiment',
        'bucket_size',
        'bucket_start',
        'session_count',
        'human_message_count',
        'is_finalized',
        'last_updated_at',
    ]
    list_filter = [
        'bucket_size',
        'is_finalized',
        'bucket_start',
    ]
    search_fields = ['experiment__name']
    readonly_fields = [
        'last_updated_at',
        'bucket_start',
        'bucket_end',
    ]
    date_hierarchy = 'bucket_start'

    actions = ['refresh_buckets', 'finalize_buckets']

    def refresh_buckets(self, request, queryset):
        """Manually refresh selected buckets."""
        from apps.experiments.tasks import update_experiment_buckets

        experiment_ids = queryset.values_list('experiment_id', flat=True).distinct()
        for exp_id in experiment_ids:
            update_experiment_buckets.delay(exp_id)

        self.message_user(
            request,
            f"Queued refresh for {len(experiment_ids)} experiments"
        )
    refresh_buckets.short_description = "Refresh selected buckets"

    def finalize_buckets(self, request, queryset):
        """Mark selected buckets as finalized."""
        count = queryset.update(is_finalized=True)
        self.message_user(request, f"Finalized {count} buckets")
    finalize_buckets.short_description = "Mark as finalized"


@admin.register(SessionStatisticsBucket)
class SessionStatisticsBucketAdmin(admin.ModelAdmin):
    list_display = [
        'session',
        'bucket_size',
        'bucket_start',
        'human_message_count',
        'is_finalized',
        'last_updated_at',
    ]
    list_filter = [
        'bucket_size',
        'is_finalized',
        'bucket_start',
    ]
    readonly_fields = [
        'last_updated_at',
        'bucket_start',
        'bucket_end',
    ]
    date_hierarchy = 'bucket_start'
```

---

## Rollback Plan

If issues arise, the system can be safely rolled back:

### Option 1: Disable Scheduled Tasks

```bash
# Temporarily disable statistics tasks
# Comment out tasks in SCHEDULED_TASKS
python manage.py setup_periodic_tasks
```

### Option 2: Revert View Changes

```python
# Revert ChatbotExperimentTableView.get_table_data() to use original subqueries
# Keep bucket tables but don't use them
```

### Option 3: Full Rollback

```bash
# Drop bucket tables
python manage.py migrate experiments <previous_migration_number>

# Revert code changes
git revert <commit_hash>
```

---

## Success Metrics

1. **Performance**:
   - Table load time < 2 seconds (target: < 1 second)
   - Database queries per page < 10 (currently 100+)

2. **Accuracy**:
   - Statistics match live queries within acceptable margin
   - Cache staleness < 2 minutes for active experiments

3. **Storage**:
   - Bucket count stays manageable (< 1M rows after compression)
   - Storage growth controlled via compression policy

4. **Reliability**:
   - Scheduled tasks run without errors
   - Compression completes within scheduled window
   - No data loss during compression

---

## Future Enhancements

1. **Real-time Updates**: Add Django signals to update buckets on message creation
2. **Trend Visualization**: Add charts showing activity over time
3. **Custom Retention**: Per-experiment compression policies
4. **Query Optimization**: Add materialized views for frequently accessed aggregates
5. **Export**: Allow exporting bucket data for analysis

---

## Conclusion

This implementation plan provides a complete, production-ready solution for time-bucketed statistics caching. The approach:

- ✅ **Solves the performance problem** with efficient bucket-based aggregation
- ✅ **Preserves historical data** with smart compression
- ✅ **Scales efficiently** as data grows
- ✅ **Requires no denormalization** (simpler than hybrid approach)
- ✅ **Provides clear migration path** with phased rollout
- ✅ **Includes comprehensive testing** and monitoring

The static compression policy (hourly → daily → monthly) provides a good balance between query performance, storage efficiency, and implementation complexity.
