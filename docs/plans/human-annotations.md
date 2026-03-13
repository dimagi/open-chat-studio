# Human Annotations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a structured human review system where teams can create annotation queues, assign reviewers to sessions/messages, collect structured feedback, and surface patterns across many reviews.

**Architecture:** A dedicated `human_annotations` Django app with `AnnotationQueue` (schema + reviewer config), `AnnotationItem` (session or message to review), `Annotation` (one reviewer's submission), and `AnnotationQueueAggregate` (aggregated results). Reuses the `FieldDefinition` schema pattern from the evaluations app. All models are team-scoped via `BaseTeamModel`.

**Tech Stack:** Django, Django-tables2, Alpine.js, HTMX, Celery + Redis (async tasks), `apps/ocs_notifications` (notifications), `apps/events` (session lifecycle)

---

## Status

### Phase 1 — Complete (PRs #2828, #2846)

The core infrastructure is fully implemented:

- **Queue CRUD** — Create/edit/delete queues with customizable JSON schemas (int, float, choice, string fields). Schema is locked once annotations begin (only `required` can change).
- **Item management** — Bulk-add sessions to a queue via `AddSessionsToQueue`; duplicate prevention; flag/unflag items with reasons.
- **Annotation workflow** — Assignees get the next unreviewed item; one review per reviewer per item; DRAFT → SUBMITTED lifecycle; automatic status updates when `num_reviews_required` is met.
- **Aggregation** — `compute_aggregates_for_queue()` runs on every submission; results stored in `AnnotationQueueAggregate`; text fields excluded.
- **Export** — CSV and JSONL export of all submitted annotations.
- **Permissions** — Queue management gated on team membership; annotation gated on assignee list.

Key files:
- Models: `apps/human_annotations/models.py`
- Queue views: `apps/human_annotations/views/queue_views.py`
- Annotation views: `apps/human_annotations/views/annotate_views.py`
- Forms: `apps/human_annotations/forms.py`
- Tests: `apps/human_annotations/tests/`

### Phase 2 — Item 9 Complete (PR #2917)

- **Session UI Integration** — "Add to Queue" button on session detail page. HTMX modal lists active queues; POST creates `AnnotationItem`; already-queued sessions shown as disabled. Queue memberships displayed as badges alongside the button. Gated behind the `flag_human_annotations` feature flag (view raises 404 and template hides the block when inactive). Fixes applied post-dogfood: modal auto-opens via Alpine `x-init`, queue name badges appear on reload, UUID text truncated in items table.

---

## Phase 2 — Remaining Work

---

### ~~Item 9: Session UI Integration — Add to Queue from Session Detail~~ ✓ Done (PR #2917)

**Goal:** Let a reviewer add the current session to an annotation queue directly from the session detail page, without navigating to the queue first.

**Context:**
- Session detail view: `apps/chatbots/views.py:530` (`chatbot_session_details_view`)
- Rendered via `apps/generics/views.py:92` (`render_session_details`)
- Session detail component template: `templates/experiments/components/experiment_details.html`
- Existing bulk-add view (queue-side): `apps/human_annotations/views/queue_views.py:159` (`AddSessionsToQueue`)
- URL namespace: `human_annotations`

**What to build:**

1. **New view `AddSessionToQueueFromSession`** in `apps/human_annotations/views/queue_views.py`:
   - GET: Returns an HTMX partial listing active queues for the team (exclude queues where session already exists)
   - POST: Accepts `queue_id`; creates `AnnotationItem(queue=queue, item_type=SESSION, session=session)`; returns a success/already-added response partial
   - URL pattern: `GET/POST /sessions/<str:session_id>/add-to-queue/` in `apps/human_annotations/urls.py`

2. **UI button** in `templates/experiments/components/experiment_details.html`:
   - "Add to Annotation Queue" button with `hx-get` triggering the modal partial
   - On success, display inline confirmation ("Added to [queue name]" or "Already in queue")

3. **HTMX modal partial** at `templates/human_annotations/add_session_to_queue_modal.html`:
   - List of active queues with radio or dropdown selection
   - Submit button; cancel closes modal
   - Show which queues already contain this session (disabled/greyed)

**Permissions:** User must be a team member; queue must be ACTIVE and belong to the same team.

**Tests:** Add to `apps/human_annotations/tests/test_views.py`:
- Session not in any queue → shows all active queues
- Session already in queue X → queue X shown as disabled
- POST adds item and returns 200
- POST with duplicate session returns appropriate message
- Non-team-member cannot access view

---

### Item 10: Queue Automation — Auto-Add Sessions Based on Criteria

**Goal:** Let queue owners define filter criteria so that sessions are automatically added to an annotation queue when they match (e.g., by experiment, tag, participant, or date).

**Context:**
- `AnnotationQueue` model: `apps/human_annotations/models.py`
- `ExperimentSession` model: `apps/experiments/models/experiment_models.py`
- Celery tasks pattern: see `apps/evaluations/tasks.py` or `apps/events/tasks.py` for examples
- Events/signals for session lifecycle: no Django signals currently; `EventLog` in `apps/events/models.py` tracks session events

**What to build:**

1. **`criteria` field on `AnnotationQueue`** — a `SanitizedJSONField` (nullable) storing a dict of filter criteria. Supported keys:
   ```json
   {
     "experiments": ["<uuid>", ...],
     "tags": ["tag-name", ...],
     "participants": ["external_id", ...],
     "session_status": "completed"
   }
   ```
   Migration: `apps/human_annotations/migrations/0003_annotationqueue_criteria.py`

2. **`evaluate_criteria(queue, session) -> bool`** function in `apps/human_annotations/criteria.py`:
   - Takes a queue and a session; returns True if session matches all defined criteria
   - Each criterion is ANDed; empty/null criteria matches nothing (automation is opt-in)

3. **Celery task `auto_add_sessions_to_queues`** in `apps/human_annotations/tasks.py`:
   - Periodic task (e.g., every 15 minutes, or triggered on-demand)
   - For each ACTIVE queue with non-null criteria, query matching sessions, bulk-create missing `AnnotationItem` records
   - Use `bulk_create(ignore_conflicts=True)` (already the pattern in `AddSessionsToQueue`)

4. **Signal hook** (optional, for immediate ingestion):
   - In `apps/human_annotations/apps.py`, connect a `post_save` signal on `ExperimentSession`
   - On session status change to "completed", check all active queues with criteria for this team
   - Only do lightweight criteria evaluation in-process; offload bulk ops to Celery

5. **UI in queue form** (`templates/human_annotations/queue_form.html`):
   - Collapsible "Automation" section after the schema builder
   - Experiment multi-select (Alpine-powered), tag input, session status dropdown
   - Save criteria alongside the queue (handled by `AnnotationQueueForm`)

**Tests:**
- `evaluate_criteria` correctly filters by experiment, tag, participant, status
- `evaluate_criteria` returns False for null/empty criteria
- Celery task creates items for matching sessions, skips duplicates
- Queue form saves and reloads criteria correctly
- Signal hook enqueues task when session completes (mock Celery)

---

### Item 11: Notification Integration

**Goal:** Notify relevant users when annotation queue activity occurs: new items added, items flagged, and review completion.

**Context:**
- Notification infrastructure: `apps/ocs_notifications/`
- Core utility: `apps/ocs_notifications/utils.py` → `create_notification(title, message, level, team, slug, event_data, permissions, links)`
- Predefined notification creators: `apps/ocs_notifications/notifications.py`
- User notification preferences model: `apps/ocs_notifications/models.py` (`UserNotificationPreferences`)
- Notification levels: INFO, WARNING, ERROR

**Three notification events to implement:**

#### 11a. New items added to a queue

- **Trigger:** After `AnnotationItem` records are created (bulk or single)
- **Recipients:** Assignees of the queue
- **Message:** "New items added to annotation queue '[queue name]' — [N] items await review."
- **Level:** INFO
- **Links:** Link to `queue:annotate` (the "get next item" URL for that queue)

#### 11b. Item flagged

- **Trigger:** `FlagItem` view (POST to `/item/<item_pk>/flag/`)
- **Recipients:** Queue creator (`queue.created_by`) + team admins
- **Message:** "Item flagged in queue '[queue name]': [flag reason]"
- **Level:** WARNING
- **Links:** Link to the specific item's annotation view

#### 11c. Queue completed (all items reviewed)

- **Trigger:** After `AnnotationItem.update_status()` when queue progress hits 100% (all items COMPLETED)
- **Recipients:** Queue creator
- **Message:** "Annotation queue '[queue name]' is complete — all [N] items have been reviewed."
- **Level:** INFO
- **Links:** Link to queue detail / export

**What to build:**

1. **`apps/human_annotations/notifications.py`** — module with three functions:
   ```python
   def notify_items_added(queue: AnnotationQueue, count: int) -> None: ...
   def notify_item_flagged(item: AnnotationItem, reason: str, flagging_user) -> None: ...
   def notify_queue_completed(queue: AnnotationQueue) -> None: ...
   ```
   Each calls `create_notification()` from `apps/ocs_notifications/utils.py`.

2. **Hook into existing views/models:**
   - `AddSessionsToQueue.post()` and `AddSessionToQueueFromSession.post()` → call `notify_items_added` after bulk_create
   - `FlagItem.post()` → call `notify_item_flagged`
   - `AnnotationQueue.get_progress()` or `AnnotationItem.update_status()` → call `notify_queue_completed` when newly completed (guard against repeat notifications)

3. **Unique `slug` identifiers** for notification deduplication:
   - Items added: `annotation_queue_items_added`
   - Item flagged: `annotation_item_flagged`
   - Queue completed: `annotation_queue_completed`

**Tests:**
- Calling `notify_items_added` creates a `NotificationEvent` for each assignee
- Calling `notify_item_flagged` creates notification for queue creator
- `notify_queue_completed` fires once when all items are COMPLETED, not on subsequent saves
- Users with notifications disabled do not receive emails (respect `UserNotificationPreferences`)

---

## Future Items (not in scope for Phase 2)

- **Item 7:** Quality metrics — inter-reviewer agreement (Cohen's kappa, Krippendorff's alpha) computed and surfaced on queue detail page
- **Item 8:** Evaluation workflow integration — trigger annotation queues from evaluator results; link `Annotation` data back to evaluation runs

---

## Implementation Order

Recommended sequence:
1. **Item 9** (Session UI) — highest user-facing value, self-contained, no new models
2. **Item 11** (Notifications) — hooks into existing views, low-risk
3. **Item 10** (Automation) — most complex; requires new model field, migration, and Celery task

Each item should be implemented with TDD (write failing test → implement → green) and committed independently.
