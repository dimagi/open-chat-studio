# Django Table View Query Optimization Implementation Plan

## Overview

Apply the query optimization pattern to 3 Django table views to reduce database query costs during pagination. The pattern separates lightweight filtering (for COUNT queries) from expensive annotations (for data display), preventing expensive database operations from executing twice per page load.

## Current State Analysis

**Existing Pattern**: Successfully implemented in `ExperimentSessionsTableView` (`apps/experiments/views/experiment.py:119-163`) and `ChatbotSessionsTableView` (`apps/chatbots/views.py:350-366`)

**Problem**: Three additional views have expensive annotations in `get_queryset()` that execute during both:
1. COUNT query (pagination calculation) - annotations not needed
2. Data query (page display) - annotations needed

This doubles the database cost for expensive operations.

**Target Views**:
1. `ChatbotExperimentTableView` - 4 Subquery annotations with aggregations
2. `DatasetSessionsSelectionTableView` - StringAgg with nested Subquery + Count annotation
3. `TranscriptAnalysisListView` - N+1 query problem (simpler fix)

### Key Discoveries:
- Optimization pattern is proven and tested in `ChatbotSessionsTableView` (`apps/chatbots/tests/test_sessions_table_view.py`)
- Research document thoroughly analyzed all `SingleTableView` subclasses
- Views have varying complexity levels but all follow same optimization approach
- Only `ChatbotExperimentTableView` has existing tests that need verification

## Desired End State

All three table views will:
1. Use lightweight `get_queryset()` for counting (filters only, no expensive annotations)
2. Override `get_table_data()` to add expensive annotations to paginated data only
3. Maintain identical functionality and display output
4. Achieve 50-97% reduction in database operations for pagination requests

**Verification**: Load each table view's page and confirm:
- Data displays correctly with all columns populated
- Pagination works properly
- Filtering/search functionality unchanged
- Page load performance improved (fewer database queries)

## What We're NOT Doing

- Not searching for additional views beyond these 3
- Not writing new tests (only verifying existing tests still pass)
- Not changing table display or column definitions
- Not modifying the underlying query annotation logic (only moving where it's applied)
- Not changing any business logic or filtering behavior

## Implementation Approach

Apply the established optimization pattern view-by-view, starting with highest impact. Each phase moves expensive annotations from `get_queryset()` to a new/updated `get_table_data()` method, keeping filters and basic `select_related()` in `get_queryset()`.

## Phase 1: Optimize ChatbotExperimentTableView

### Overview
Move 4 expensive Subquery annotations from `get_queryset()` to `get_table_data()` to prevent them from executing during pagination COUNT queries.

### Changes Required:

#### 1. Update ChatbotExperimentTableView
**File**: `apps/chatbots/views.py:170-223`

**Current Structure**:
```python
def get_queryset(self):
    # Define 4 expensive subqueries (lines 173-198)
    session_count_subquery = ...
    participant_count_subquery = ...
    messages_count_subquery = ...
    last_message_subquery = ...

    # Apply all annotations in get_queryset (lines 200-208)
    query_set = (
        self.model.objects.get_all()
        .filter(team=self.request.team, working_version__isnull=True, pipeline__isnull=False)
        .select_related("team", "owner")
        .annotate(session_count=Subquery(session_count_subquery))      # ⚠️ EXPENSIVE
        .annotate(participant_count=Subquery(participant_count_subquery))  # ⚠️ EXPENSIVE
        .annotate(messages_count=Subquery(messages_count_subquery))    # ⚠️ EXPENSIVE
        .annotate(last_message=Subquery(last_message_subquery))        # ⚠️ EXPENSIVE
        .order_by(F("last_message").desc(nulls_last=True))
    )
    # Apply filters (lines 210-222)
    show_archived = self.request.GET.get("show_archived") == "on"
    if not show_archived:
        query_set = query_set.filter(is_archived=False)

    search = self.request.GET.get("search")
    if search:
        query_set = similarity_search(...)
    return query_set
```

**New Structure**:
```python
def get_queryset(self):
    """Returns a lightweight queryset for counting. Expensive annotations are added in get_table_data()."""
    query_set = (
        self.model.objects.get_all()
        .filter(team=self.request.team, working_version__isnull=True, pipeline__isnull=False)
        .select_related("team", "owner")
    )

    # Apply filters but no expensive annotations
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
    """Add expensive annotations only to the paginated data, not for counting."""
    from apps.experiments.models import ExperimentSession

    queryset = super().get_table_data()

    # Define subqueries (move from get_queryset)
    session_count_subquery = (
        ExperimentSession.objects.filter(experiment_id=OuterRef("pk"))
        .values("experiment_id")
        .annotate(count=Count("id"))
        .values("count")
    )

    participant_count_subquery = (
        ExperimentSession.objects.filter(experiment_id=OuterRef("pk"))
        .values("experiment_id")
        .annotate(count=Count("participant_id", distinct=True))
        .values("count")
    )

    messages_count_subquery = (
        ChatMessage.objects.filter(chat__experiment_session__experiment_id=OuterRef("pk"))
        .values("chat__experiment_session__experiment_id")
        .annotate(count=Count("id"))
        .values("count")
    )

    last_message_subquery = (
        ChatMessage.objects.filter(chat__experiment_session__experiment_id=OuterRef("pk"))
        .order_by("-created_at")
        .values("created_at")[:1]
    )

    # Add expensive annotations only to paginated data
    queryset = (
        queryset
        .annotate(session_count=Subquery(session_count_subquery))
        .annotate(participant_count=Subquery(participant_count_subquery))
        .annotate(messages_count=Subquery(messages_count_subquery))
        .annotate(last_message=Subquery(last_message_subquery))
        .order_by(F("last_message").desc(nulls_last=True))
    )
    return queryset
```

**Key Changes**:
1. Remove all 4 `.annotate()` calls and `.order_by()` from `get_queryset()`
2. Keep filters (`show_archived`, `search`) in `get_queryset()`
3. Keep basic `select_related("team", "owner")` for FK fields used in filtering
4. Add new `get_table_data()` method with all annotation logic
5. Move subquery definitions into `get_table_data()`
6. Add ordering in `get_table_data()` since it depends on `last_message` annotation

### Success Criteria:

#### Automated Verification:
- [ ] All unit tests pass: `pytest apps/chatbots/tests/test_chatbot_views.py::test_chatbot_experiment_table_view -v`
- [ ] Type checking passes: `npm run type-check`
- [ ] Linting passes: `inv ruff`
- [ ] No new migrations generated: `python manage.py makemigrations --dry-run --check`

#### Manual Verification:
- [ ] Navigate to Chatbots table view (URL: `/teams/<team_slug>/chatbots/`)
- [ ] Verify all columns display correctly: name, participants, sessions, messages, last message
- [ ] Test pagination with multiple pages of chatbots
- [ ] Test "Show archived" toggle functionality
- [ ] Test search functionality by chatbot name/description
- [ ] Verify ordering by last message timestamp works correctly
- [ ] Check browser console for no JavaScript errors

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation that the manual testing was successful before proceeding to Phase 2.

---

## Phase 2: Optimize DatasetSessionsSelectionTableView

### Overview
Move `annotate_with_versions_list()` (StringAgg) and `message_count` (Count) annotations from `get_queryset()` to `get_table_data()` to prevent expensive aggregations during pagination COUNT queries.

### Changes Required:

#### 1. Update DatasetSessionsSelectionTableView
**File**: `apps/evaluations/views/dataset_views.py:219-251`

**Current Structure**:
```python
def get_queryset(self):
    timezone = self.request.session.get("detected_tz", None)
    filter_params = FilterParams.from_request(self.request)

    message_filter = ChatMessageFilter()
    base_messages = ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
    filtered_messages = message_filter.apply(base_messages, filter_params, timezone)
    has_messages = Exists(filtered_messages)

    query_set = (
        ExperimentSession.objects.filter(team=self.request.team)
        .filter(has_messages)
        .select_related("team", "participant__user", "chat", "experiment")
        .annotate_with_versions_list()  # ⚠️ EXPENSIVE - StringAgg with nested Subquery
    )

    session_filter = ExperimentSessionFilter()
    query_set = session_filter.apply(query_set, filter_params=filter_params, timezone=timezone)

    query_set = query_set.annotate(
        message_count=Coalesce(
            Count("chat__messages", filter=Q(...), distinct=True),  # ⚠️ EXPENSIVE
            0,
        )
    ).order_by("experiment__name")

    return query_set
```

**New Structure**:
```python
def get_queryset(self):
    """Returns a lightweight queryset for counting. Expensive annotations are added in get_table_data()."""
    timezone = self.request.session.get("detected_tz", None)
    filter_params = FilterParams.from_request(self.request)

    # Get filtered message IDs more efficiently
    message_filter = ChatMessageFilter()
    base_messages = ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
    filtered_messages = message_filter.apply(base_messages, filter_params, timezone)

    # Use Exists for filtering instead of Count with IN subquery - avoids cartesian product
    has_messages = Exists(filtered_messages)

    # Build the query with basic filtering only
    query_set = (
        ExperimentSession.objects.filter(team=self.request.team)
        .filter(has_messages)  # Filter early with Exists
        .select_related("team", "participant__user", "chat", "experiment")
    )

    # Apply session filter (this will add first_message_created_at and last_message_created_at)
    session_filter = ExperimentSessionFilter()
    query_set = session_filter.apply(query_set, filter_params=filter_params, timezone=timezone)

    return query_set.order_by("experiment__name")

def get_table_data(self):
    """Add expensive annotations only to the paginated data, not for counting."""
    queryset = super().get_table_data()

    # Get filter params for message count
    timezone = self.request.session.get("detected_tz", None)
    filter_params = FilterParams.from_request(self.request)
    message_filter = ChatMessageFilter()
    base_messages = ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
    filtered_messages = message_filter.apply(base_messages, filter_params, timezone)

    # Add expensive annotations only to paginated data
    queryset = queryset.annotate_with_versions_list().annotate(
        message_count=Coalesce(
            Count("chat__messages", filter=Q(chat__messages__in=filtered_messages.values("pk")), distinct=True),
            0,
        )
    )
    return queryset
```

**Key Changes**:
1. Remove `.annotate_with_versions_list()` from `get_queryset()`
2. Remove `.annotate(message_count=...)` from `get_queryset()`
3. Keep all filters, `Exists()`, and `select_related()` in `get_queryset()`
4. Keep ordering in `get_queryset()` since it doesn't depend on removed annotations
5. Add new `get_table_data()` method with both annotation calls
6. Recreate filter_params and filtered_messages in `get_table_data()` for message_count

### Success Criteria:

#### Automated Verification:
- [ ] All unit tests pass: `pytest apps/evaluations/ -v`
- [ ] Type checking passes: `npm run type-check`
- [ ] Linting passes: `inv ruff`
- [ ] No new migrations generated: `python manage.py makemigrations --dry-run --check`

#### Manual Verification:
- [ ] Navigate to dataset session selection view (create new dataset flow)
- [ ] Verify all columns display correctly: experiment name, participant, versions, message count
- [ ] Test pagination with multiple pages of sessions
- [ ] Test date range filtering functionality
- [ ] Test message type filtering (if available in UI)
- [ ] Verify message count values are accurate
- [ ] Verify versions column shows correct experiment version tags
- [ ] Check that selecting sessions for dataset creation still works

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation that the manual testing was successful before proceeding to Phase 3.

---

## Phase 3: Fix TranscriptAnalysisListView N+1 Query

### Overview
Add `select_related("experiment")` to prevent N+1 query problem when displaying experiment names in the table.

### Changes Required:

#### 1. Update TranscriptAnalysisListView
**File**: `apps/analysis/views.py:36-37`

**Current Code**:
```python
def get_queryset(self):
    return TranscriptAnalysis.objects.filter(team=self.request.team)
```

**New Code**:
```python
def get_queryset(self):
    return TranscriptAnalysis.objects.filter(team=self.request.team).select_related("experiment")
```

**Why This Works**:
- Table displays `experiment.name` via accessor (`apps/analysis/tables.py:10`)
- Without `select_related()`: 1 query for analyses + N queries for experiments = N+1 queries
- With `select_related()`: 1 query with JOIN = single efficient query
- This is simpler than the other two phases - no `get_table_data()` needed

### Success Criteria:

#### Automated Verification:
- [ ] All unit tests pass: `pytest apps/analysis/ -v`
- [ ] Type checking passes: `npm run type-check`
- [ ] Linting passes: `inv ruff`
- [ ] No new migrations generated: `python manage.py makemigrations --dry-run --check`

#### Manual Verification:
- [ ] Navigate to transcript analysis list view (URL: `/teams/<team_slug>/analysis/`)
- [ ] Verify experiment name column displays correctly
- [ ] Test with multiple transcript analyses
- [ ] Verify all other columns display correctly
- [ ] Check pagination if enough data exists
- [ ] Check browser console for no JavaScript errors

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation that the manual testing was successful.

---

## Testing Strategy

### Unit Testing Approach
- **Phase 1**: Existing test at `apps/chatbots/tests/test_chatbot_views.py:41-57` verifies view rendering and pipeline filtering. Run to ensure no regressions.
- **Phase 2**: No existing tests. Rely on manual verification.
- **Phase 3**: No existing tests. Rely on manual verification.

### Reference Test Pattern
The comprehensive test suite at `apps/chatbots/tests/test_sessions_table_view.py` demonstrates the ideal testing approach:
- Verifies expensive annotations NOT in base `get_queryset()`
- Verifies expensive annotations ARE in `get_table_data()`
- Tests pagination with annotations
- Tests empty states and null values

This pattern could be replicated for Phases 1 and 2 in future work, but is out of scope for this plan.

### Manual Testing Focus
For each phase, manual testing should verify:
1. **Data Accuracy**: All table columns display correct values
2. **Functionality**: Filtering, search, pagination work identically
3. **Performance**: Page loads feel faster (subjective but noticeable)
4. **No Regressions**: Related features (export, selection, etc.) still work

---

## Performance Considerations

### Expected Impact

**Phase 1: ChatbotExperimentTableView**
- Before: ~4100 database operations for 1000 chatbots (4 subqueries × 1000 rows for COUNT + 4 × 25 for page)
- After: ~101 operations (1 COUNT + 4 subqueries × 25 rows for page)
- **Savings**: ~97% reduction in operations

**Phase 2: DatasetSessionsSelectionTableView**
- Before: StringAgg + Count execute on full queryset before pagination
- After: StringAgg + Count execute only on paginated subset (typically 25-50 rows)
- **Savings**: Proportional to dataset size; 95%+ for large datasets (1000+ sessions)

**Phase 3: TranscriptAnalysisListView**
- Before: N+1 queries (1 + N where N = number of analyses displayed)
- After: 1 query with JOIN
- **Savings**: Eliminates N extra queries (e.g., 50 analyses = 49 eliminated queries)

### Database Load
All three optimizations reduce database load by:
1. Preventing unnecessary computation during COUNT queries
2. Reducing total number of queries per page load
3. Applying expensive operations only to small paginated subsets

---

## Migration Notes

**No database migrations required** - this is purely a query optimization refactoring.

All changes are at the Django ORM query level. Database schema remains unchanged.

---

## References

- Reference implementation: `apps/experiments/views/experiment.py:119-163` (ExperimentSessionsTableView)
- Reference subclass: `apps/chatbots/views.py:350-366` (ChatbotSessionsTableView)
- Reference tests: `apps/chatbots/tests/test_sessions_table_view.py`
- Django-tables2 docs: https://django-tables2.readthedocs.io/
- Git branch: `sk/session-query`
