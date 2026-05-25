# Dataset "Add Sessions" Page Simplification

**Date:** 2026-05-25
**Related PR:** #3354 (introduced the page being simplified)
**Status:** Draft

## Problem

The dataset "Add Sessions" sub-page (introduced in #3354) overloads users with three overlapping controls for what is effectively one decision: *which sessions, and which of their messages, get cloned into the dataset.*

The current UI shows simultaneously:

1. **"Add" mode toggle** — Selected only / All matching filters / Sample (controls which sessions).
2. **"Messages to clone" radio** — All messages / Filtered messages only (controls which messages within each session, for message-mode datasets).
3. **Two checkbox columns in the table** — "All" (purple) and "Filtered" (pink) — per-row version of #2.

Problems:

- **The "Filtered" column is dead UI.** Its `@change` handlers (`updateFilteredSessions`, `toggleFilteredSessions`) live in the legacy `dataset-mode-selector.js` bundle, but the new page loads `human_annotations-bundle.js`, which has no such handlers. Clicking the pink checkboxes does nothing for the form payload. Server-side already uses only the top-level `message_scope` radio.
- **The "Messages to clone" radio and per-row columns are conceptually redundant.** Users see two ways to make the same choice and it's not clear which wins.
- **The radio is shown for session-mode datasets** where it has no effect.
- **The radio is shown even when no filters are active**, where "Filtered messages only" is meaningless.

Result: a confusing screen for what should be a simple "pick what to add" task.

## Goal

Reduce the "Add Sessions" page to a single clear flow:

> *Filter the sessions you want → pick the scope (selected / all matching / sample) → optionally choose all-messages vs. filtered-messages (only when that choice is meaningful) → submit.*

No behavior changes on the server. No new features. Same POST payload, same Celery tasks.

## Decisions (locked from brainstorming)

1. **One global** all-vs-filtered choice, not per-row.
2. **Placed inline** with the filter bar (visually tying "filtered messages" to "the filters above").
3. **Keep** Selected / All matching / Sample modes; redesign the bar so it reads more like a sentence.
4. **Show** the Clone toggle **only for message-mode datasets AND only when ≥ 1 filter is active**.
5. **Remove** the "Filtered" checkbox column entirely.

## Design

### Layout

```
Datasets > Sample Customer Messages > Add Sessions

Add Sessions to "Sample Customer Messages"

┌──────────────────────────────────────────────────────────────────────────┐
│ [▼ Filter] [⏱ Date Range ▼]      Clone: ◉ All messages  ○ Filtered only │
│                                  (right-aligned, conditional)            │
└──────────────────────────────────────────────────────────────────────────┘

Add to dataset:  [● Selected (1)] [○ All 47 matching] [○ Sample 20% ▭]    47 sessions    [Add 1 session →] [Cancel]

┌──────────────────────────────────────────────────────────────────────────┐
│ [☐]  Experiment            Participant                  Last Message ... │
│ [☑]  Customer Support Bot  eve.davis@example.com        2026-05-20 ...   │
│ [☐]  Programming Helper    david.brown@example.com      2026-05-19 ...   │
└──────────────────────────────────────────────────────────────────────────┘
```

### Behavior rules

| Dataset mode | Filters active? | Clone toggle | `message_scope` submitted |
|---|---|---|---|
| Session-mode | n/a | hidden | n/a (always clones whole sessions on the server) |
| Message-mode | none | hidden | `all` (default; sent via hidden input) |
| Message-mode | ≥ 1 filter | visible, inline with filter bar | user's choice (`all` default on entry) |

When the user clears all filters while message-mode and `messageScope === 'filtered'`, the Clone toggle hides and `messageScope` resets to `'all'` (so we never submit a stale value the user can't see).

### Action bar (one row)

- Three pills in a `join` group: Selected / All matching / Sample. Active pill is `btn-primary`, others `btn-ghost`. Same as today.
- Pill labels embed the current count:
  - `Selected (N)` — N from `selectedSessionIds.size`.
  - `All N matching` — N from `totalCount`.
  - `Sample X%` — X from `samplePercent`. The `%` input remains inline when active.
- Live session-count text ("47 sessions") and the primary action button live on the right of the same row.
- Primary button label mirrors current dynamic logic (`Add N to dataset` / `Add all N to dataset` / `Add ~N to dataset`).
- **Remove** the current "Row selections do not affect this mode" hint and the dimming overlay. Instead, **hide the table's checkbox column** when `mode !== 'selected'` via `x-show` on the column header and cells.

### Sessions table

- Single checkbox column on the left, header label empty (current `verbose_name="All"` is removed). Header checkbox = select-all-on-page (existing behavior).
- The `clone_filtered_only` column is removed from `EvaluationSessionsSelectionTable`.
- All other columns unchanged: Experiment, Participant, Last Message, Versions, Messages, View Session action.

## Implementation outline

### Files changed

| File | Change |
|---|---|
| `templates/evaluations/add_sessions.html` | Layout rewrite per the design above. Remove the "Messages to clone" bar; replace with conditional inline Clone toggle in the filter row. Restructure the scope bar to the unified one-row sentence. Drop the "Row selections do not affect this mode" hint and the dimming. |
| `apps/evaluations/tables.py` | Remove the `clone_filtered_only` column from `EvaluationSessionsSelectionTable`. Clear the `verbose_name` on the remaining `selection` column (header becomes empty) or rename to something neutral. |
| `assets/javascript/apps/human_annotations/session-selector.js` | Add `hasActiveFilters` getter (derived from `filterParams`, ignoring non-filter params like `page`, `dataset_id`, etc.). Add a hook so the page-level Alpine extension can react when filters clear (auto-reset `messageScope` to `'all'`). Keep all existing behavior. |
| `templates/evaluations/add_sessions.html` (script block) | Continue to extend the base component with `messageScope`. Compute toggle visibility from `dataset.evaluation_mode !== 'session' && hasActiveFilters`. Add watcher: when `hasActiveFilters` flips false, set `messageScope = 'all'`. |
| `apps/evaluations/views/dataset_views.py` | No semantic change. Verify defaults: when `message_scope` is missing or empty, treat as `'all'` (already the case). |
| `apps/evaluations/tests/test_evaluation_dataset_session_clone.py` | Drop assertions tied to the removed column. Existing assertions on POST payloads keep working (server-side contract unchanged). |
| New test (view-level) | Render `evaluations/add_sessions.html` for each combination (session-mode / message-mode × no filters / with filter) and assert presence/absence of the Clone toggle markup. |

### What is **not** changing

- POST contract (`mode`, `session_ids`, `sample_percent`, `message_scope`) — identical.
- Celery tasks `create_dataset_from_sessions_task` and `create_dataset_from_session_messages_task` — identical.
- The `dataset_sessions_count` endpoint — identical.
- The legacy `dataset-mode-selector.js` bundle and `dataset_sessions_selection_json` endpoint — left alone (already flagged for cleanup in #3428).
- No new feature flags. No data migrations. No model changes.

## Testing

### Automated

- Update existing tests in `apps/evaluations/tests/test_evaluation_dataset_session_clone.py` that reference the dropped column.
- Add a small view-level test that renders the page in four states and asserts the Clone toggle markup is present/absent as expected:
  - session-mode + no filters → absent
  - session-mode + filter → absent
  - message-mode + no filters → absent
  - message-mode + filter → present, default `all`
- Add a test that posts the form from a message-mode dataset with no filters and confirms `message_scope='all'` was submitted (hidden input default behavior).

### Manual

Run the dev server and exercise each combination on a seeded team:

1. Session-mode dataset, no filters → no Clone toggle; submit "All matching" → success.
2. Session-mode dataset, with filter → no Clone toggle; submit "Selected" → success.
3. Message-mode dataset, no filters → no Clone toggle; submit "Selected" → server clones all messages.
4. Message-mode dataset, with filter → Clone toggle appears; pick "Filtered only"; submit "All matching" → server clones only filter-matching messages.
5. Message-mode dataset, with filter → set "Filtered only", clear all filters → toggle disappears and state resets to "All".
6. Switch between Selected → All matching → Sample → confirm table checkbox column hides/appears cleanly with no flash.

## Out of scope

- Per-row clone-scope (rejected during brainstorming — user picked one global control).
- Removing Sample mode (kept).
- Cleaning up the legacy `dataset-mode-selector.js` bundle (covered by #3428).
- Any change to session-mode vs. message-mode dataset semantics.
- Any change to filters themselves.
