# Performance Research: Table Views and Dashboard

This document contains the research findings for performance issues identified in table views with aggregations/filtering and the team dashboard.

## Executive Summary

Performance issues stem from several common patterns across all affected views:

1. **Pre-pagination subquery annotations** - Expensive computations run on entire datasets
2. **Multiple sequential count queries** - Dashboard runs 8+ separate COUNT operations
3. **Complex nested EXISTS subqueries** - Tag filtering creates 6+ nested queries
4. **Missing database indexes** - Critical indexes for team+date, participant, and experiment lookups
5. **Python-side aggregations** - Data loaded into memory for processing instead of database aggregation
6. **No cache invalidation** - Stale data persists until TTL expiration

## Affected Components

| Component | Location | Primary Issue |
|-----------|----------|---------------|
| ChatbotExperimentTableView | `apps/chatbots/views.py:164-235` | 4 expensive subquery annotations pre-pagination |
| ChatbotSessionsTableView | `apps/chatbots/views.py:365-380` | Double annotation layers + complex version aggregation |
| DatasetSessionsSelectionTableView | `apps/evaluations/views/dataset_views.py:230-267` | Nested OuterRef subqueries + message count aggregation |
| DashboardService | `apps/dashboard/services.py:1-679` | 8 sequential counts, tag filtering complexity |

---

## 1. ChatbotExperimentTableView Analysis

### Current Implementation

**Location:** `apps/chatbots/views.py` (lines 164-235)

The view applies 4 expensive Subquery annotations to the **entire queryset** before pagination:

```python
def get_queryset(self):
    queryset = Experiment.objects.filter(team=self.request.team)
    queryset = queryset.annotate(
        session_count=Subquery(...),      # Counts ExperimentSession with channel filtering
        participant_count=Subquery(...),  # Counts distinct participants per experiment
        interaction_count=Subquery(...),  # Counts all Trace records
        last_activity=Subquery(...),      # Gets latest Trace timestamp for sorting
    )
    return queryset.order_by("-last_activity")
```

### Issues Identified

1. **Pre-pagination annotation problem**
   - All 4 subqueries evaluate for EVERY experiment, not just the paginated page
   - The `ORDER BY last_activity` requires full queryset evaluation

2. **Expensive aggregations**
   - `participant_count`: Uses `DISTINCT COUNT` with platform enum filtering
   - `interaction_count`: Uses `NOT IN` subquery (inefficient) instead of LEFT JOIN
   - `session_count`: Filters by experiment channel

3. **Filter complexity** (`apps/experiments/filters.py:44-186`)
   - `ChatMessageTagsFilter.apply_all_of()`: Creates 2N `Exists()` subqueries for N tags
   - `VersionsFilter`: Similar nested subquery pattern

### Query Pattern

```sql
-- Generated query structure (simplified)
SELECT
    e.*,
    (SELECT COUNT(*) FROM experiment_session WHERE ...) AS session_count,
    (SELECT COUNT(DISTINCT participant_id) FROM experiment_session WHERE ...) AS participant_count,
    (SELECT COUNT(*) FROM trace WHERE experiment_id NOT IN (SELECT ...) OR ...) AS interaction_count,
    (SELECT timestamp FROM trace WHERE ... ORDER BY timestamp DESC LIMIT 1) AS last_activity
FROM experiment e
WHERE e.team_id = %s
ORDER BY last_activity DESC
```

### Missing Indexes

- `ExperimentSession(experiment_id, team_id)` - For session counting
- `Trace(experiment_id, timestamp)` - For interaction counts and last_activity

---

## 2. ChatbotSessionsTableView Analysis

### Current Implementation

**Location:** `apps/chatbots/views.py` (lines 365-380)

Inherits from `ExperimentSessionsTableView` which applies filter annotations pre-pagination:

```python
# Parent class (apps/experiments/views/experiment.py:121-165)
def get_queryset(self):
    queryset = ExperimentSession.objects.filter(...)
    queryset = queryset.annotate_with_last_message_created_at()
    queryset = queryset.annotate_with_first_message_created_at()
    return queryset

# Child class adds more annotations
def get_table_data(self):
    data = super().get_table_data()
    return data.annotate_with_message_count().annotate_with_versions_list()
```

### Issues Identified

1. **Double annotation layers**
   - Pre-pagination: `last_message_created_at`, `first_message_created_at`
   - Post-pagination: `message_count`, `versions_list`

2. **Complex version aggregation** (`apps/experiments/models.py:1686-1700`)
   ```python
   def annotate_with_versions_list(self):
       version_tags_subquery = Subquery(
           CustomTaggedItem.objects.filter(
               content_type=message_ct,
               object_id__in=ChatMessage.objects.filter(
                   chat_id=OuterRef(OuterRef("chat_id"))  # Double nested!
               ).values("id"),
               tag__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
           )
           .annotate(versions=StringAgg("tag__name", delimiter=", ", distinct=True))
           .values("versions")[:1]
       )
   ```
   - Double nested `OuterRef` creates complex correlated subquery
   - `StringAgg` with distinct requires sorting for each row

3. **ContentType lookup not cached**
   ```python
   message_ct = ContentType.objects.get_for_model(ChatMessage)  # Runs each time
   ```

### Missing Indexes

- `ChatMessage(chat_id)` - For message subqueries
- `CustomTaggedItem(content_type_id, tag_id)` - For version tag lookups

---

## 3. DatasetSessionsSelectionTableView Analysis

### Current Implementation

**Location:** `apps/evaluations/views/dataset_views.py` (lines 230-267)

```python
def get_queryset(self):
    queryset = get_base_session_queryset(self.request)  # Applies complex filters
    queryset = queryset.annotate_with_versions_list().annotate(
        message_count=Coalesce(Count("chat__messages", distinct=True), 0)
    )
    return queryset.select_related(...)
```

### Issues Identified

1. **Same version annotation issues** as ChatbotSessionsTableView

2. **Message count uses JOIN instead of subquery**
   - `Count("chat__messages", distinct=True)` creates cartesian product risk
   - Should use Subquery annotation instead

3. **Multiple filter subqueries** (`apps/experiments/filters.py:168-196`)
   - `ExperimentSessionFilter.prepare_queryset()` adds 2 annotation subqueries
   - Each filter in chain may add additional subqueries

### Filter Chain Complexity

```python
filters: ClassVar[Sequence[ColumnFilter]] = [
    ParticipantFilter(),                    # Simple FK filter
    TimestampFilter(...last_message...),    # Filter on annotated field
    TimestampFilter(...first_message...),   # Filter on annotated field
    ChatMessageTagsFilter(),                # Tags Exists subquery
    VersionsFilter(),                       # Multiple nested Exists subqueries
    ChannelsFilter(),
    ExperimentFilter(),
    StatusFilter(),
    RemoteIdFilter(),
]
```

---

## 4. Dashboard Service Analysis

### Current Implementation

**Location:** `apps/dashboard/services.py` (lines 1-679)

The dashboard service makes multiple separate queries for each metric.

### get_overview_stats() - Critical Issue

**Location:** Lines 654-664

```python
def get_overview_stats(self, filters):
    querysets = self.get_filtered_queryset_base(filters)
    return {
        "total_experiments": querysets["experiments"].count(),           # Query 1
        "total_participants": querysets["participants"].count(),         # Query 2
        "total_sessions": querysets["sessions"].count(),                 # Query 3
        "total_messages": querysets["messages"].count(),                 # Query 4
        "experiments_with_sessions": querysets["experiments"]
            .filter(...).distinct().count(),                             # Query 5
        "active_participants": querysets["sessions"]
            .values("participant").distinct().count(),                   # Query 6
        "completed_sessions": querysets["sessions"]
            .filter(ended_at__isnull=False).count(),                     # Query 7
        # ... more counts
    }
```

**Problem:** 8 separate database queries for a single endpoint call.

### Tag Filtering - Critical Issue

**Location:** Lines 116-184

```python
if tag_ids:
    # Creates 6 separate EXISTS subqueries for filtering
    tag_on_chat = Exists(CustomTaggedItem.objects.filter(...))
    tag_on_msg = Exists(
        CustomTaggedItem.objects.filter(
            object_id__in=Subquery(
                ChatMessage.objects.filter(
                    chat=OuterRef(OuterRef("chat_id"))  # Double nested!
                ).values("id")
            ),
        )
    )
    sessions = sessions.annotate(_tchat=tag_on_chat, _tmsg=tag_on_msg)
                       .filter(Q(_tchat=True) | Q(_tmsg=True))
```

### Python-Side Aggregations

1. **Session length histogram** (lines 439-449)
   ```python
   session_lengths = querysets["sessions"].filter(ended_at__isnull=False)
       .annotate(duration=...).values_list("duration", flat=True)
   session_lengths = [d.total_seconds() / 60 for d in session_lengths]  # Load ALL to memory
   histogram = self._create_histogram(session_lengths, bins=10)          # Python calculation
   ```

2. **Tag statistics** (lines 529-542)
   ```python
   for tagged_item in tagged_messages:  # Loop through all tags
       # Python aggregation instead of GROUP BY
   ```

### Missing Indexes for Dashboard

- `ExperimentSession(team_id, created_at)` - Date-range filtering
- `ExperimentSession(team_id, ended_at)` - Completion stats
- `Trace(team_id, timestamp)` - Response time queries
- `ExperimentChannel(team_id, platform)` - Platform filtering

---

## 5. Core Data Model Analysis

### Model Relationships and Cardinality

```
Team
├─ Experiments: ~50-200 per team
│  ├─ ExperimentSession: ~100-10,000 per experiment
│  │  ├─ Chat: 1:1 with session
│  │  │  └─ ChatMessage: ~5-500 per chat
│  │  └─ Traces: ~1-100 per session
│  └─ ExperimentChannel: ~1-5 per experiment
│
└─ Participants: ~100-5,000 per team
   ├─ ParticipantData: 1-200 per participant
   └─ ExperimentSession: 1-50 per participant
```

### Existing Indexes (All Models)

| Model | Current Indexes |
|-------|-----------------|
| Experiment | `["team", "is_archived", "working_version"]` |
| ExperimentSession | `["chat", "team"]`, `["chat", "team", "ended_at"]` |
| ChatMessage | `["chat", "created_at"]`, `["chat", "message_type", "created_at"]` |
| Trace | `status` (field-level only) |
| ParticipantData | `["experiment"]` |
| CustomTaggedItem | `["content_type", "object_id"]` |

### Critical Missing Indexes

| Model | Missing Index | Used By | Impact |
|-------|--------------|---------|--------|
| ExperimentSession | `(experiment_id, team_id)` | Chatbot views, dashboard | HIGH |
| ExperimentSession | `(team_id, created_at)` | Dashboard date filtering | HIGH |
| ExperimentSession | `(team_id, ended_at)` | Completion statistics | HIGH |
| ExperimentSession | `(participant_id, team_id)` | Participant views | MEDIUM |
| Trace | `(experiment_id, timestamp)` | Trend data, interaction counts | HIGH |
| Trace | `(team_id, timestamp)` | Dashboard response times | HIGH |
| CustomTaggedItem | `(content_type_id, tag_id)` | Version filtering | MEDIUM |
| ParticipantData | `(participant_id)` | Participant data lookups | MEDIUM |

---

## 6. Identified Query Anti-Patterns

### Anti-Pattern 1: Pre-Pagination Annotations

**Problem:** Expensive subqueries run on entire dataset before pagination

```python
# Bad
queryset = Model.objects.annotate(
    expensive_count=Subquery(...)
).order_by("-expensive_field")[:25]

# Good
ids = Model.objects.order_by(...).values_list("id", flat=True)[:25]
queryset = Model.objects.filter(id__in=ids).annotate(expensive_count=Subquery(...))
```

### Anti-Pattern 2: Multiple Sequential Counts

**Problem:** Each count() is a separate database query

```python
# Bad
return {
    "count_a": queryset.count(),
    "count_b": queryset.filter(status="active").count(),
    "count_c": queryset.values("field").distinct().count(),
}

# Good
return queryset.aggregate(
    count_a=Count("id"),
    count_b=Count("id", filter=Q(status="active")),
    count_c=Count("field", distinct=True),
)
```

### Anti-Pattern 3: Nested OuterRef

**Problem:** Creates complex correlated subqueries

```python
# Bad
Subquery(
    Model.objects.filter(
        field__in=OtherModel.objects.filter(
            ref=OuterRef(OuterRef("field"))  # Double nested
        ).values("id")
    )
)

# Better: Use JOIN or restructure query
```

### Anti-Pattern 4: Python-Side Aggregations

**Problem:** Loads data into memory for processing

```python
# Bad
data = list(queryset.values_list("field", flat=True))
result = sum(data) / len(data)

# Good
result = queryset.aggregate(avg=Avg("field"))["avg"]
```

### Anti-Pattern 5: Uncached ContentType Lookups

**Problem:** ContentType query runs on every call

```python
# Bad
def annotate_something(self):
    ct = ContentType.objects.get_for_model(Model)  # Query each time

# Good
from functools import lru_cache

@lru_cache(maxsize=32)
def get_content_type_id(model):
    return ContentType.objects.get_for_model(model).id
```

---

## 7. Performance Impact Summary

### Estimated Query Counts Per Page Load

| View | Current Queries | Estimated Time |
|------|-----------------|----------------|
| ChatbotExperimentTableView | 5-10 | 500ms-2s |
| ChatbotSessionsTableView | 8-15 | 1-3s |
| DatasetSessionsSelectionTableView | 10-20 | 1-4s |
| Dashboard (all endpoints) | 30-50 | 5-15s |

### Root Cause Distribution

| Cause | Impact % | Affected Components |
|-------|----------|---------------------|
| Pre-pagination annotations | 30% | All table views |
| Multiple COUNT queries | 20% | Dashboard overview |
| Tag filtering complexity | 20% | All views with tag filters |
| Missing indexes | 15% | All queries |
| Python aggregations | 10% | Dashboard analytics |
| Uncached lookups | 5% | Version annotations |

---

## 8. Caching Analysis

### Current Caching Implementation

**DashboardCache Model** (`apps/dashboard/models.py:9-46`)
- Fixed 30-minute TTL
- Cache key includes filter hash
- No cache invalidation on data changes
- Separate cache check per endpoint (adds latency)

### Cache Issues

1. **No invalidation strategy** - Data stays stale until TTL expires
2. **No cache warming** - First load always slow
3. **Per-endpoint caching** - No shared cache for common calculations
4. **Fixed TTL** - Not appropriate for all data types

---

## 9. Files Reference

### Primary Files Requiring Changes

| File | Lines | Issues |
|------|-------|--------|
| `apps/chatbots/views.py` | 164-380 | Pre-pagination annotations |
| `apps/experiments/views/experiment.py` | 121-165 | Filter annotations pre-pagination |
| `apps/experiments/models.py` | 1662-1700 | Expensive annotation methods |
| `apps/experiments/filters.py` | 44-196 | Complex Exists() patterns |
| `apps/evaluations/views/dataset_views.py` | 230-267 | Nested subqueries |
| `apps/dashboard/services.py` | 1-679 | Multiple sequential queries |
| `apps/dashboard/views.py` | 1-220 | API endpoint structure |

### Related Model Files

| File | Purpose |
|------|---------|
| `apps/experiments/models.py` | Experiment, ExperimentSession, Participant |
| `apps/chat/models.py` | Chat, ChatMessage |
| `apps/trace/models.py` | Trace (missing indexes) |
| `apps/annotations/models.py` | CustomTaggedItem |
| `apps/channels/models.py` | ExperimentChannel |

---

## Next Steps

Based on this research, the following design document outlines a comprehensive optimization strategy:

- [Performance Optimization Design](./performance_optimization_design.md)
