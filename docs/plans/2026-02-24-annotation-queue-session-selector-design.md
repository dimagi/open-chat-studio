# Annotation Queue Session Selector — Design

**Date:** 2026-02-24
**Branch:** sk/annotations-III

## Problem

The current "Add Sessions to Queue" flow shows a basic scrollable checkbox list of the 200 most recent sessions. There is no filtering, no pagination, and no way to search or narrow sessions by experiment, participant, date, tags, etc.

## Goal

Replace the simple checkbox list with the same filterable, paginated session table used by the evaluations dataset creation form. Users will be able to apply filters (experiment, participant, date range, tags) and select sessions using checkboxes before submitting.

## Approach

New annotation-queue-specific endpoints and a focused Alpine.js component (Option A). The evaluations `datasetModeSelector` component carries significant complexity (mode switching, CSV upload, manual entry) that does not belong in the annotation queue context.

---

## Components

### Backend

**`AnnotationSessionsSelectionTable`** (`apps/human_annotations/tables.py`)
- Single `selection` checkbox column using `evaluations/session_checkbox.html` template
- CSS class `session-checkbox`, `@change="updateSelectedSessions()"`
- Header checkbox for select-all: `@change="toggleSelectedSessions()"`
- Columns: experiment, participant, last_message, message_count, and a "View Session" chip action
- `Meta.model = ExperimentSession`

**`AnnotationQueueSessionsTableView`** (`apps/human_annotations/views/queue_views.py`)
- `LoginAndTeamRequiredMixin`, `PermissionRequiredMixin`, `SingleTableView`
- `permission_required = "human_annotations.add_annotationitem"`
- Table: `AnnotationSessionsSelectionTable`; template: `table/single_table_lazy_pagination.html`; paginator: `LazyPaginator`
- Queryset: filter by team + `ExperimentSessionFilter`, annotate `message_count`, select related fields

**`annotation_queue_sessions_json`** (`apps/human_annotations/views/queue_views.py`)
- `@login_and_team_required`, `@permission_required("human_annotations.add_annotationitem")`
- Applies same `ExperimentSessionFilter` as the table view
- Returns `JsonResponse` of `external_id` list

**New URL patterns** (`apps/human_annotations/urls.py`)
```
queue/<int:pk>/sessions-table/  →  AnnotationQueueSessionsTableView
queue/<int:pk>/sessions-json/   →  annotation_queue_sessions_json
```

**`AddSessionsToQueue.post()`** updated to:
- Read `session_ids` from POST (comma-separated `external_id` values from hidden field)
- Look up sessions via `external_id__in` scoped to team
- Keep existing duplicate-handling logic (`ignore_conflicts=True`, report skipped count)

### Frontend

**`assets/javascript/apps/human_annotations/session-selector.js`**
- `window.annotationQueueSessionSelector = function(options) {...}`
- Properties: `selectedSessionIds` (Set), `allSessionIds` (Set), `sessionIdsFetchUrl`, `errorMessages`
- Methods:
  - `init()` — loads session IDs, sets up `filter:change` and `dataset-mode:table-update` listeners
  - `updateSelectedSessions()` — reads `.session-checkbox` checkboxes, updates Set and hidden field, updates header checkbox state
  - `toggleSelectedSessions()` — select all / deselect all using `allSessionIds`
  - `clearAllSelections()` — clears Set and unchecks all checkboxes
  - `restoreCheckboxStates()` — re-checks selected sessions after HTMX table reload
  - `loadSessionIds()` — fetches `allSessionIds` from JSON endpoint
  - `validateAndSubmit(e)` — prevents submit if nothing selected

**Webpack entry** (`webpack.config.js`)
```js
'human_annotations': './assets/javascript/apps/human_annotations/session-selector.js'
```
Outputs to `static/js/human_annotations-bundle.js`.

**`templates/human_annotations/add_items_from_sessions.html`** rewritten:
- Alpine `x-data="annotationQueueSessionSelector({sessionIdsFetchUrl: '...'})"` wrapper
- `{% include "experiments/filters.html" %}`
- Selected count display: "N of M sessions selected" + Clear button
- `<div id="sessions-table" data-url="...">` for HTMX lazy table load
- Hidden field: `<input type="hidden" name="session_ids">` synced from Alpine component
- Submit + Cancel buttons; error message display

---

## Data Flow

1. **GET** `/queue/{pk}/add-sessions/` — `AddSessionsToQueue.get()` passes filter context + queue to template
2. Alpine component inits → `loadSessionIds()` fetches `queue/{pk}/sessions-json/` → populates `allSessionIds`
3. Sessions table loads lazily via HTMX from `queue/{pk}/sessions-table/`
4. User applies filters → `filter:change` event → HTMX reloads table + `loadSessionIds()` re-fetches
5. After table reload, `restoreCheckboxStates()` re-checks previously selected visible sessions
6. User checks sessions → `updateSelectedSessions()` updates `selectedSessionIds` + hidden field
7. **POST** `/queue/{pk}/add-sessions/` — reads `session_ids`, resolves via `external_id__in`, bulk-creates `AnnotationItem` records, reports added/skipped

---

## Testing

- `AnnotationQueueSessionsTableView`: returns filtered sessions, applies `ExperimentSessionFilter`, annotates `message_count`
- `annotation_queue_sessions_json`: returns correct `external_id` list, respects filter params, scoped to team
- `AddSessionsToQueue.post()`: update existing tests to use `session_ids` with `external_id` values instead of `sessions` with internal IDs
