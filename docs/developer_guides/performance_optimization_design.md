# Performance Optimization Design

This document outlines a comprehensive strategy to optimize table view and dashboard performance in Open Chat Studio. The design addresses issues identified in the [Performance Research](./performance_research.md) document.

## Design Principles

1. **Optimize for common cases** - Focus on the most frequent query patterns
2. **Scalable solutions** - Design for 10x data growth
3. **Maintainable code** - Prefer clear patterns over micro-optimizations
4. **Incremental implementation** - Phase changes to minimize risk
5. **Backwards compatible** - Existing functionality must continue to work

---

## Architecture Overview

The optimization strategy consists of five main components:

```
┌─────────────────────────────────────────────────────────┐
│                  Performance Optimization               │
├─────────────┬─────────────┬─────────────┬──────────────┤
│  Database   │   Query     │   Caching   │    Filter    │
│  Indexing   │ Optimization│   Strategy  │ Optimization │
├─────────────┼─────────────┼─────────────┼──────────────┤
│ - New       │ - Post-     │ - Signal    │ - Optimized  │
│   indexes   │   pagination│   based     │   tag        │
│ - Partial   │   annotation│   invalidation│ filtering  │
│   indexes   │ - Combined  │ - Cache     │ - Subquery   │
│             │   aggregates│   warming   │   reduction  │
└─────────────┴─────────────┴─────────────┴──────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │    Denormalization      │
              │  (Selective Use Only)   │
              └─────────────────────────┘
```

---

## 1. Database Indexing Strategy

### 1.1 Critical New Indexes

Add these indexes in order of priority:

#### Phase 1 - High Impact (Week 1)

```python
# apps/experiments/models.py - ExperimentSession
class Meta:
    indexes = [
        # Existing
        models.Index(fields=["chat", "team"]),
        models.Index(fields=["chat", "team", "ended_at"]),

        # NEW - Critical for table views and dashboard
        models.Index(
            fields=["team", "experiment"],
            name="idx_session_team_experiment"
        ),
        models.Index(
            fields=["team", "created_at"],
            name="idx_session_team_created"
        ),
        models.Index(
            fields=["team", "ended_at"],
            name="idx_session_team_ended"
        ),
        models.Index(
            fields=["experiment", "participant"],
            name="idx_session_exp_participant"
        ),
    ]
```

```python
# apps/trace/models.py - Trace
class Meta:
    indexes = [
        models.Index(
            fields=["experiment", "timestamp"],
            name="idx_trace_experiment_time"
        ),
        models.Index(
            fields=["team", "timestamp"],
            name="idx_trace_team_time"
        ),
        models.Index(
            fields=["session"],
            name="idx_trace_session"
        ),
    ]
```

#### Phase 2 - Medium Impact (Week 2)

```python
# apps/annotations/models.py - CustomTaggedItem
class Meta:
    indexes = [
        # Existing
        models.Index(fields=["content_type", "object_id"]),

        # NEW - For version and tag filtering
        models.Index(
            fields=["content_type", "tag"],
            name="idx_taggeditem_type_tag"
        ),
        models.Index(
            fields=["team", "tag"],
            name="idx_taggeditem_team_tag"
        ),
    ]
```

```python
# apps/experiments/models.py - ParticipantData
class Meta:
    indexes = [
        # Existing
        models.Index(fields=["experiment"]),

        # NEW
        models.Index(
            fields=["participant"],
            name="idx_participantdata_participant"
        ),
    ]
```

```python
# apps/channels/models.py - ExperimentChannel
class Meta:
    indexes = [
        models.Index(
            fields=["team", "platform"],
            name="idx_channel_team_platform"
        ),
    ]
```

### 1.2 Partial Indexes for Common Filters

```python
# apps/experiments/models.py - ExperimentSession
from django.contrib.postgres.indexes import PostgresPartialIndex

class Meta:
    indexes = [
        # ... other indexes ...

        # Partial index for completed sessions only
        models.Index(
            fields=["team", "ended_at"],
            name="idx_session_completed",
            condition=Q(ended_at__isnull=False),
        ),

        # Partial index for active sessions
        models.Index(
            fields=["team", "created_at"],
            name="idx_session_active",
            condition=Q(status="active"),
        ),
    ]
```

### 1.3 Migration Strategy

```python
# Example migration file
from django.db import migrations, models

class Migration(migrations.Migration):
    atomic = False  # Allow concurrent index creation

    operations = [
        migrations.AddIndex(
            model_name="experimentsession",
            index=models.Index(
                fields=["team", "experiment"],
                name="idx_session_team_experiment"
            ),
        ),
        migrations.RunSQL(
            sql="CREATE INDEX CONCURRENTLY idx_session_team_created ON experiments_experimentsession (team_id, created_at)",
            reverse_sql="DROP INDEX CONCURRENTLY idx_session_team_created",
        ),
    ]
```

---

## 2. Query Optimization Patterns

### 2.1 Post-Pagination Annotation Pattern

Move expensive annotations after pagination to reduce computation.

#### Current (Bad)

```python
class ChatbotExperimentTableView(SingleTableView):
    def get_queryset(self):
        queryset = Experiment.objects.filter(team=self.request.team)
        # Expensive annotations on ENTIRE dataset
        queryset = queryset.annotate(
            session_count=Subquery(...),
            participant_count=Subquery(...),
            interaction_count=Subquery(...),
            last_activity=Subquery(...),
        )
        return queryset.order_by("-last_activity")
```

#### Proposed (Good)

```python
class ChatbotExperimentTableView(SingleTableView):
    def get_queryset(self):
        """Return base queryset with only lightweight annotations for sorting."""
        queryset = Experiment.objects.filter(team=self.request.team)

        # Only annotate what's needed for sorting/filtering
        # Use a lightweight last_activity from a subquery
        queryset = queryset.annotate(
            last_activity=Subquery(
                Trace.objects.filter(
                    experiment_id=OuterRef("pk")
                ).order_by("-timestamp").values("timestamp")[:1]
            )
        )
        return queryset.order_by(F("last_activity").desc(nulls_last=True))

    def get_table_data(self):
        """Apply expensive annotations only to paginated data."""
        paginated_data = super().get_table_data()

        # Now annotate only the paginated page (typically 25 items)
        return paginated_data.annotate(
            session_count=self._session_count_subquery(),
            participant_count=self._participant_count_subquery(),
            interaction_count=self._interaction_count_subquery(),
        )

    def _session_count_subquery(self):
        """Optimized session count using filtered subquery."""
        return Subquery(
            ExperimentSession.objects.filter(
                experiment_id=OuterRef("pk"),
                team_id=self.request.team.id,
            ).values("experiment_id").annotate(
                count=Count("id")
            ).values("count")[:1],
            output_field=IntegerField(),
        )
```

### 2.2 Combined Aggregate Pattern

Replace multiple count() calls with single aggregate().

#### Current (Bad)

```python
def get_overview_stats(self, filters):
    querysets = self.get_filtered_queryset_base(filters)
    return {
        "total_experiments": querysets["experiments"].count(),
        "total_participants": querysets["participants"].count(),
        "total_sessions": querysets["sessions"].count(),
        "total_messages": querysets["messages"].count(),
        "completed_sessions": querysets["sessions"].filter(ended_at__isnull=False).count(),
    }
```

#### Proposed (Good)

```python
def get_overview_stats(self, filters):
    """Get all overview stats in a single database query."""
    querysets = self.get_filtered_queryset_base(filters)

    # Build unified aggregation query
    stats = querysets["sessions"].aggregate(
        total_sessions=Count("id"),
        completed_sessions=Count("id", filter=Q(ended_at__isnull=False)),
        active_participants=Count("participant", distinct=True),
        total_messages=Count("chat__messages"),
    )

    # Experiment stats in separate query (different table)
    experiment_stats = querysets["experiments"].aggregate(
        total_experiments=Count("id"),
        experiments_with_sessions=Count(
            "id",
            filter=Q(sessions__isnull=False),
            distinct=True
        ),
    )

    # Participant count (potentially expensive, consider caching)
    participant_count = querysets["participants"].count()

    return {
        **stats,
        **experiment_stats,
        "total_participants": participant_count,
    }
```

### 2.3 Window Function Pattern

Replace multiple subqueries with window functions where applicable.

#### Current (Bad)

```python
def annotate_with_last_message_created_at(self):
    return self.annotate(
        last_message_created_at=Subquery(
            ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
            .order_by("-created_at")
            .values("created_at")[:1]
        )
    )

def annotate_with_first_message_created_at(self):
    return self.annotate(
        first_message_created_at=Subquery(
            ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
            .order_by("created_at")
            .values("created_at")[:1]
        )
    )
```

#### Proposed (Good)

```python
def annotate_with_message_timestamps(self):
    """Annotate with first and last message timestamps in single subquery."""
    # Use a single subquery that returns both values
    message_stats = ChatMessage.objects.filter(
        chat_id=OuterRef("chat_id")
    ).values("chat_id").annotate(
        first_created=Min("created_at"),
        last_created=Max("created_at"),
        msg_count=Count("id"),
    )

    return self.annotate(
        first_message_created_at=Subquery(
            message_stats.values("first_created")[:1]
        ),
        last_message_created_at=Subquery(
            message_stats.values("last_created")[:1]
        ),
        message_count=Subquery(
            message_stats.values("msg_count")[:1]
        ),
    )
```

### 2.4 Simplified Version Annotation

Replace complex nested OuterRef with a more efficient approach.

#### Current (Bad)

```python
def annotate_with_versions_list(self):
    message_ct = ContentType.objects.get_for_model(ChatMessage)  # Uncached query
    version_tags_subquery = Subquery(
        CustomTaggedItem.objects.filter(
            content_type=message_ct,
            object_id__in=ChatMessage.objects.filter(
                chat_id=OuterRef(OuterRef("chat_id"))  # Double nested
            ).values("id"),
            tag__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
        )
        .values("content_type_id")
        .annotate(versions=StringAgg("tag__name", delimiter=", ", distinct=True))
        .values("versions")[:1]
    )
    return self.annotate(experiment_versions=Coalesce(version_tags_subquery, Value("")))
```

#### Proposed (Good)

```python
from functools import lru_cache

@lru_cache(maxsize=32)
def _get_content_type_id(model):
    """Cache ContentType lookups."""
    return ContentType.objects.get_for_model(model).id

def annotate_with_versions_list(self):
    """Optimized version annotation using JOIN instead of nested subquery."""
    message_ct_id = _get_content_type_id(ChatMessage)

    # Alternative approach: Use a lateral join or restructured subquery
    version_tags_subquery = Subquery(
        CustomTaggedItem.objects.filter(
            content_type_id=message_ct_id,
            tag__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
        ).filter(
            # Use EXISTS with JOIN instead of IN subquery
            object_id__in=Subquery(
                ChatMessage.objects.filter(
                    chat_id=OuterRef("chat_id")
                ).values("id")
            )
        ).values("object_id").annotate(
            versions=StringAgg(
                "tag__name",
                delimiter=", ",
                distinct=True,
                ordering="tag__name"
            )
        ).values("versions")[:1],
        output_field=CharField(),
    )

    return self.annotate(
        experiment_versions=Coalesce(
            version_tags_subquery,
            Value(""),
            output_field=CharField()
        )
    )
```

---

## 3. Caching Strategy

### 3.1 Signal-Based Cache Invalidation

Implement automatic cache invalidation when data changes.

```python
# apps/dashboard/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from apps.experiments.models import ExperimentSession
from apps.chat.models import ChatMessage
from apps.dashboard.models import DashboardCache

@receiver([post_save, post_delete], sender=ExperimentSession)
def invalidate_session_cache(sender, instance, **kwargs):
    """Invalidate dashboard cache when sessions change."""
    DashboardCache.invalidate_for_team(
        instance.team,
        patterns=["overview_stats", "session_analytics", "bot_performance"]
    )

@receiver([post_save, post_delete], sender=ChatMessage)
def invalidate_message_cache(sender, instance, **kwargs):
    """Invalidate dashboard cache when messages change."""
    try:
        team = instance.chat.experiment_session.team
        DashboardCache.invalidate_for_team(
            team,
            patterns=["message_volume", "tag_analytics", "overview_stats"]
        )
    except AttributeError:
        pass  # Message might not have a session

# apps/dashboard/models.py - Add invalidation method
class DashboardCache(models.Model):
    # ... existing fields ...

    @classmethod
    def invalidate_for_team(cls, team, patterns=None):
        """Invalidate cache entries for a team."""
        queryset = cls.objects.filter(team=team)
        if patterns:
            from django.db.models import Q
            q = Q()
            for pattern in patterns:
                q |= Q(cache_key__contains=pattern)
            queryset = queryset.filter(q)
        queryset.delete()
```

### 3.2 Tiered Caching with Different TTLs

Use appropriate cache durations based on data volatility.

```python
# apps/dashboard/services.py

class CacheConfig:
    """Cache configuration for different data types."""

    # Overview stats change frequently
    OVERVIEW_STATS_TTL = 5 * 60  # 5 minutes

    # Historical analytics change less frequently
    HISTORICAL_ANALYTICS_TTL = 30 * 60  # 30 minutes

    # Trend data can be cached longer
    TREND_DATA_TTL = 60 * 60  # 1 hour

    # User engagement data
    ENGAGEMENT_TTL = 15 * 60  # 15 minutes

class DashboardService:
    def get_overview_stats(self, filters):
        cache_key = f"overview_stats_{self._cache_key(filters)}"
        cached = DashboardCache.get_cached_data(
            self.team,
            cache_key,
            ttl=CacheConfig.OVERVIEW_STATS_TTL
        )
        if cached:
            return cached

        # ... compute stats ...

        DashboardCache.set_cached_data(
            self.team,
            cache_key,
            data,
            ttl=CacheConfig.OVERVIEW_STATS_TTL
        )
        return data
```

### 3.3 Cache Warming

Pre-compute expensive metrics during off-peak hours.

```python
# apps/dashboard/tasks.py
from celery import shared_task
from apps.teams.models import Team
from apps.dashboard.services import DashboardService

@shared_task
def warm_dashboard_caches():
    """Pre-compute dashboard metrics for active teams."""
    from django.utils import timezone
    from datetime import timedelta

    # Get teams with recent activity
    active_teams = Team.objects.filter(
        membership__user__last_login__gte=timezone.now() - timedelta(days=7)
    ).distinct()

    for team in active_teams:
        try:
            service = DashboardService(team)

            # Warm common filter combinations
            common_filters = [
                {},  # No filters
                {"date_range": "7d"},
                {"date_range": "30d"},
            ]

            for filters in common_filters:
                service.get_overview_stats(filters)
                service.get_session_analytics_data("daily", filters)
                service.get_message_volume_data("daily", filters)

        except Exception as e:
            logger.error(f"Cache warming failed for team {team.id}: {e}")

# Schedule in Celery beat
CELERY_BEAT_SCHEDULE = {
    "warm-dashboard-caches": {
        "task": "apps.dashboard.tasks.warm_dashboard_caches",
        "schedule": crontab(minute=0, hour="*/2"),  # Every 2 hours
    },
}
```

### 3.4 Request-Level Caching

Cache expensive lookups within a single request.

```python
# apps/utils/cache.py
from functools import wraps
from threading import local

_request_cache = local()

def request_cached(key_func):
    """Cache function results for the duration of a request."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not hasattr(_request_cache, "data"):
                _request_cache.data = {}

            key = key_func(*args, **kwargs)
            if key not in _request_cache.data:
                _request_cache.data[key] = func(*args, **kwargs)
            return _request_cache.data[key]
        return wrapper
    return decorator

def clear_request_cache():
    """Clear request cache. Call in middleware."""
    if hasattr(_request_cache, "data"):
        _request_cache.data.clear()

# Usage
@request_cached(lambda model: f"content_type_{model.__name__}")
def get_content_type_for_model(model):
    return ContentType.objects.get_for_model(model)
```

---

## 4. Filter Optimization

### 4.1 Optimized Tag Filtering

Replace multiple EXISTS subqueries with a more efficient approach.

#### Current (Bad)

```python
def get_filtered_queryset_base(self, filters):
    if tag_ids:
        # 6 separate EXISTS subqueries
        tag_on_chat = Exists(...)
        tag_on_msg = Exists(
            CustomTaggedItem.objects.filter(
                object_id__in=Subquery(
                    ChatMessage.objects.filter(
                        chat=OuterRef(OuterRef("chat_id"))
                    ).values("id")
                )
            )
        )
        sessions = sessions.filter(Q(_tchat=True) | Q(_tmsg=True))
```

#### Proposed (Good)

```python
def get_filtered_queryset_base(self, filters):
    if tag_ids:
        # Use a CTE or single subquery approach
        sessions_with_tags = ExperimentSession.objects.filter(
            team=self.team
        ).filter(
            Q(
                # Chat has tag
                chat__in=Subquery(
                    CustomTaggedItem.objects.filter(
                        content_type=chat_ct,
                        tag_id__in=tag_ids,
                        team=self.team,
                    ).values("object_id")
                )
            ) | Q(
                # Messages in chat have tag
                chat__messages__in=Subquery(
                    CustomTaggedItem.objects.filter(
                        content_type=message_ct,
                        tag_id__in=tag_ids,
                        team=self.team,
                    ).values("object_id")
                )
            )
        ).distinct()

        sessions = sessions.filter(pk__in=sessions_with_tags.values("pk"))
```

### 4.2 Filter Application Order

Apply selective filters first to reduce dataset size early.

```python
class ExperimentSessionFilter:
    """Optimized filter with strategic ordering."""

    # Order filters by selectivity (most selective first)
    filters: ClassVar[Sequence[ColumnFilter]] = [
        # High selectivity - apply first
        ExperimentFilter(),      # Usually filters to 1-5 experiments
        StatusFilter(),          # Often filters to specific status
        ParticipantFilter(),     # Usually specific participants

        # Medium selectivity
        ChannelsFilter(),
        RemoteIdFilter(),

        # Low selectivity - apply last
        TimestampFilter(...),    # Date ranges are usually broad
        ChatMessageTagsFilter(), # Complex, apply after reducing dataset
        VersionsFilter(),        # Complex, apply last
    ]
```

### 4.3 Lazy Filter Evaluation

Don't apply expensive filters unless the column is visible/sorted.

```python
class ChatbotSessionsTable(ExperimentSessionsTable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Only annotate versions if column is visible
        if "versions" in self.visible_columns:
            self.data = self.data.annotate_with_versions_list()

        # Only annotate message count if needed
        if "message_count" in self.visible_columns or self.order_by == "message_count":
            self.data = self.data.annotate_with_message_count()
```

---

## 5. Denormalization Strategy

### 5.1 When to Denormalize

Denormalize only when:
- Data is read 100x more than written
- Aggregation is very expensive
- Real-time accuracy is not critical
- Maintenance overhead is acceptable

### 5.2 Recommended Denormalizations

#### Session Statistics on Experiment

```python
# apps/experiments/models.py
class Experiment(BaseTeamModel, VersionsMixin):
    # Existing fields...

    # Denormalized statistics (updated via signals)
    cached_session_count = models.PositiveIntegerField(default=0)
    cached_participant_count = models.PositiveIntegerField(default=0)
    cached_last_activity = models.DateTimeField(null=True, blank=True)

    def update_cached_stats(self):
        """Update denormalized statistics."""
        from django.db.models import Count, Max

        stats = ExperimentSession.objects.filter(
            experiment=self
        ).aggregate(
            session_count=Count("id"),
            participant_count=Count("participant", distinct=True),
            last_activity=Max("created_at"),
        )

        self.cached_session_count = stats["session_count"] or 0
        self.cached_participant_count = stats["participant_count"] or 0
        self.cached_last_activity = stats["last_activity"]
        self.save(update_fields=[
            "cached_session_count",
            "cached_participant_count",
            "cached_last_activity"
        ])

# Signal to update stats
@receiver(post_save, sender=ExperimentSession)
def update_experiment_stats(sender, instance, created, **kwargs):
    if created:
        # Defer update to avoid N+1 in bulk operations
        from apps.experiments.tasks import update_experiment_cached_stats
        update_experiment_cached_stats.delay(instance.experiment_id)
```

#### Message Timestamps on Session

```python
# apps/experiments/models.py
class ExperimentSession(BaseTeamModel):
    # Existing fields...

    # Denormalized message timestamps
    first_message_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    message_count = models.PositiveIntegerField(default=0)

# Signal handler
@receiver(post_save, sender=ChatMessage)
def update_session_message_stats(sender, instance, created, **kwargs):
    if created:
        try:
            session = instance.chat.experiment_session
            session.message_count = F("message_count") + 1
            session.last_message_at = instance.created_at
            if session.first_message_at is None:
                session.first_message_at = instance.created_at
            session.save(update_fields=[
                "message_count", "last_message_at", "first_message_at"
            ])
        except ExperimentSession.DoesNotExist:
            pass
```

### 5.3 Materialized Views (Advanced)

For complex dashboard aggregations, consider PostgreSQL materialized views.

```sql
-- Create materialized view for dashboard metrics
CREATE MATERIALIZED VIEW dashboard_team_metrics AS
SELECT
    es.team_id,
    DATE_TRUNC('day', es.created_at) AS date,
    e.id AS experiment_id,
    COUNT(DISTINCT es.id) AS session_count,
    COUNT(DISTINCT es.participant_id) AS participant_count,
    COUNT(cm.id) AS message_count,
    COUNT(DISTINCT CASE WHEN es.ended_at IS NOT NULL THEN es.id END) AS completed_sessions
FROM experiments_experimentsession es
JOIN experiments_experiment e ON e.id = es.experiment_id
LEFT JOIN chat_chat c ON c.id = es.chat_id
LEFT JOIN chat_chatmessage cm ON cm.chat_id = c.id
GROUP BY es.team_id, DATE_TRUNC('day', es.created_at), e.id;

-- Create indexes on materialized view
CREATE INDEX idx_dtm_team_date ON dashboard_team_metrics(team_id, date);

-- Refresh periodically
CREATE OR REPLACE FUNCTION refresh_dashboard_metrics()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY dashboard_team_metrics;
END;
$$ LANGUAGE plpgsql;
```

```python
# apps/dashboard/tasks.py
@shared_task
def refresh_dashboard_materialized_view():
    """Refresh the dashboard materialized view."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY dashboard_team_metrics")
```

---

## 6. Implementation Phases

### Phase 1: Quick Wins (Week 1-2)

**Estimated Impact: 40-50% performance improvement**

1. **Add critical database indexes**
   - ExperimentSession indexes for team+experiment, team+created_at
   - Trace indexes for experiment+timestamp, team+timestamp
   - Migration with CONCURRENTLY to avoid locks

2. **Cache ContentType lookups**
   - Add `@lru_cache` to ContentType queries
   - Clear cache on app startup

3. **Combine dashboard count queries**
   - Replace 8 separate count() with single aggregate()
   - Expected: 8 queries → 2-3 queries

4. **Fix uncached repeated queries**
   - Request-level caching for repeated lookups

### Phase 2: Query Optimization (Week 3-4)

**Estimated Impact: 30-40% additional improvement**

1. **Implement post-pagination annotation pattern**
   - Modify ChatbotExperimentTableView
   - Modify ChatbotSessionsTableView
   - Modify DatasetSessionsSelectionTableView

2. **Optimize version annotation**
   - Simplify nested OuterRef
   - Cache ContentType lookups

3. **Combine message timestamp annotations**
   - Single subquery for first/last/count

4. **Reorder filter application**
   - Apply selective filters first

### Phase 3: Caching Enhancement (Week 5-6)

**Estimated Impact: 20-30% additional improvement**

1. **Implement signal-based cache invalidation**
   - Add signals for Session, Message changes
   - Invalidate relevant cache patterns

2. **Add tiered TTL caching**
   - Different TTLs for different data types

3. **Implement cache warming**
   - Celery task for pre-computation
   - Focus on active teams

4. **Add monitoring for cache effectiveness**
   - Hit/miss ratio tracking
   - Cache size monitoring

### Phase 4: Advanced Optimizations (Week 7-8)

**Estimated Impact: 10-20% additional improvement**

1. **Selective denormalization**
   - Add cached stats to Experiment model
   - Add message timestamps to Session model

2. **Optimize tag filtering**
   - Restructure to avoid double nested OuterRef
   - Consider raw SQL for complex cases

3. **Consider materialized views**
   - For dashboard historical data
   - Schedule regular refresh

4. **Performance testing and tuning**
   - Load testing with production-like data
   - Query plan analysis
   - Further index optimization

---

## 7. Monitoring and Metrics

### 7.1 Key Performance Indicators

Track these metrics to measure success:

| Metric | Current Baseline | Target | How to Measure |
|--------|------------------|--------|----------------|
| Chatbot list page load | 2-4s | <500ms | Django Debug Toolbar |
| Sessions table load | 3-5s | <800ms | Browser DevTools |
| Dashboard initial load | 5-15s | <2s | API response times |
| Database query count/page | 30-50 | <15 | Query logging |
| Cache hit ratio | N/A | >80% | Cache metrics |

### 7.2 Query Monitoring

```python
# settings.py - Enable query logging in development
LOGGING = {
    'handlers': {
        'query_log': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': '/tmp/queries.log',
        },
    },
    'loggers': {
        'django.db.backends': {
            'handlers': ['query_log'],
            'level': 'DEBUG',
        },
    },
}

# Custom middleware for query counting
class QueryCountMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.db import connection
        initial_queries = len(connection.queries)

        response = self.get_response(request)

        total_queries = len(connection.queries) - initial_queries
        response['X-Query-Count'] = str(total_queries)

        if total_queries > 20:
            logger.warning(
                f"High query count: {total_queries} queries for {request.path}"
            )

        return response
```

### 7.3 Cache Monitoring

```python
# apps/dashboard/models.py
class DashboardCache(models.Model):
    # ... existing fields ...
    hit_count = models.PositiveIntegerField(default=0)

    @classmethod
    def get_cached_data(cls, team, cache_key, ttl=None):
        try:
            cache_entry = cls.objects.get(
                team=team,
                cache_key=cache_key,
                expires_at__gt=timezone.now()
            )
            # Track hit
            cls.objects.filter(pk=cache_entry.pk).update(
                hit_count=F("hit_count") + 1
            )
            return cache_entry.data
        except cls.DoesNotExist:
            return None

    @classmethod
    def get_cache_stats(cls, team):
        """Get cache effectiveness stats."""
        from django.db.models import Sum, Count, Avg

        return cls.objects.filter(team=team).aggregate(
            total_entries=Count("id"),
            total_hits=Sum("hit_count"),
            avg_hits_per_entry=Avg("hit_count"),
        )
```

---

## 8. Testing Strategy

### 8.1 Performance Test Suite

```python
# tests/performance/test_table_views.py
import pytest
from django.test import override_settings
from apps.utils.factories import ExperimentFactory, ExperimentSessionFactory

@pytest.mark.performance
class TestChatbotTableViewPerformance:

    @pytest.fixture
    def large_dataset(self, team_with_users, db):
        """Create realistic dataset for performance testing."""
        experiments = ExperimentFactory.create_batch(50, team=team_with_users)
        for exp in experiments:
            ExperimentSessionFactory.create_batch(
                100,
                experiment=exp,
                team=team_with_users
            )
        return team_with_users

    def test_chatbot_list_query_count(self, client, large_dataset):
        """Ensure chatbot list view uses acceptable query count."""
        client.force_login(large_dataset.members.first())

        with self.assertNumQueries(15):  # Target: <15 queries
            response = client.get(
                reverse("chatbots:table", kwargs={"team_slug": large_dataset.slug})
            )

        assert response.status_code == 200

    def test_chatbot_list_response_time(self, client, large_dataset, benchmark):
        """Benchmark chatbot list view response time."""
        client.force_login(large_dataset.members.first())
        url = reverse("chatbots:table", kwargs={"team_slug": large_dataset.slug})

        result = benchmark(client.get, url)

        assert result.status_code == 200
        # benchmark plugin will track timing
```

### 8.2 Query Plan Analysis

```python
# tests/performance/test_query_plans.py
from django.db import connection

def test_session_query_uses_index(team_with_users, experiment):
    """Verify that session queries use appropriate indexes."""
    from apps.experiments.models import ExperimentSession

    queryset = ExperimentSession.objects.filter(
        team=team_with_users,
        experiment=experiment,
    )

    # Get query plan
    sql = str(queryset.query)
    with connection.cursor() as cursor:
        cursor.execute(f"EXPLAIN ANALYZE {sql}")
        plan = cursor.fetchall()

    plan_text = "\n".join([row[0] for row in plan])

    # Verify index usage
    assert "Index Scan" in plan_text or "Index Only Scan" in plan_text
    assert "Seq Scan" not in plan_text  # Should not do sequential scan
```

---

## 9. Rollback Plan

Each phase should be independently reversible:

### Phase 1 Rollback
- Indexes can be dropped without affecting functionality
- Cache changes are additive

### Phase 2 Rollback
- Keep old annotation methods alongside new ones
- Feature flag to switch between implementations

```python
# apps/experiments/models.py
class ExperimentSessionQuerySet(models.QuerySet):
    def annotate_with_message_timestamps(self):
        if settings.USE_OPTIMIZED_ANNOTATIONS:
            return self._optimized_message_timestamps()
        return self._legacy_message_timestamps()
```

### Phase 3 Rollback
- Cache invalidation signals can be disabled
- Fall back to TTL-only expiration

### Phase 4 Rollback
- Denormalized fields are optional
- Materialized views can be dropped

---

## 10. Success Criteria

The optimization project is successful when:

1. **Performance targets met**
   - Table views load in <1 second (p95)
   - Dashboard loads in <3 seconds (p95)
   - Query count per page <15

2. **Scalability verified**
   - Performance maintains with 10x data growth
   - Load testing passes with 100 concurrent users

3. **No regressions**
   - All existing tests pass
   - No functional changes to user experience

4. **Maintainable**
   - Code is documented and follows patterns
   - Monitoring is in place for ongoing performance tracking

---

## Appendix A: SQL Reference

### Useful EXPLAIN ANALYZE Queries

```sql
-- Check index usage for session queries
EXPLAIN ANALYZE
SELECT * FROM experiments_experimentsession
WHERE team_id = 1 AND experiment_id = 100
ORDER BY created_at DESC;

-- Check dashboard count performance
EXPLAIN ANALYZE
SELECT
    COUNT(*) AS total_sessions,
    COUNT(*) FILTER (WHERE ended_at IS NOT NULL) AS completed_sessions,
    COUNT(DISTINCT participant_id) AS active_participants
FROM experiments_experimentsession
WHERE team_id = 1
AND created_at >= NOW() - INTERVAL '7 days';

-- Check tag filtering performance
EXPLAIN ANALYZE
SELECT DISTINCT es.id
FROM experiments_experimentsession es
LEFT JOIN chat_chat c ON c.id = es.chat_id
LEFT JOIN annotations_customtaggeditem ct ON ct.object_id = c.id
WHERE es.team_id = 1
AND ct.tag_id IN (1, 2, 3);
```

### Index Maintenance

```sql
-- Check index usage stats
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;

-- Find unused indexes
SELECT
    indexrelname,
    idx_scan,
    pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE idx_scan = 0
AND schemaname = 'public';

-- Rebuild bloated indexes
REINDEX INDEX CONCURRENTLY idx_session_team_experiment;
```

---

## Appendix B: Related Documentation

- [Performance Research](./performance_research.md) - Detailed analysis of current issues
- [Dynamic Filters](./dynamic_filters.md) - Filter system documentation
- [Common Practices](./common_practises.md) - Django patterns used in the project
