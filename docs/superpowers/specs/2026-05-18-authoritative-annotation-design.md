# Authoritative annotation flag for multi-reviewer queues

Issue: [dimagi/open-chat-studio#3342](https://github.com/dimagi/open-chat-studio/issues/3342)

## Background

`apps/human_annotations` (introduced in PR #3319) supports multi-reviewer queues. Each `AnnotationItem` represents a session (or message) and can hold up to N submitted `Annotation` rows, one per reviewer. Aggregates (`AnnotationQueueAggregate.aggregates`) are computed by averaging numeric / counting categorical values across **all** submitted annotations for every item.

When reviewers disagree, there is no way to mark one of their annotations as the canonical answer. The workaround today is for both reviewers to manually edit their annotations until they match, which is fragile and loses the original disagreement signal.

## Goal

Add a per-annotation `is_authoritative` flag, enforced as at-most-one per item, that:

1. Lets queue admins resolve multi-reviewer conflicts by picking one annotation as canonical.
2. Drives queue aggregation ("concordance" metrics): authoritative if set, otherwise fall back to all submitted annotations.
3. Drives queue completion: an item is `COMPLETED` only when single-reviewer (auto) or it has an authoritative annotation. Multi-reviewer items with all reviews submitted but no authoritative pick sit in a new `AWAITING_RESOLUTION` state.

## Non-goals

- A separate "gold label" annotation type (explicitly rejected in the issue).
- Automatic conflict detection / flagging.
- Allowing reviewers (non-admin) to mark authoritativeness on their own annotations.
- Filtering or transforming exports beyond surfacing the bool.

## Data model

`apps/human_annotations/models.py`:

### `Annotation` — three new fields

- `is_authoritative: BooleanField(default=False)`
- `authoritative_set_by: ForeignKey(User, on_delete=SET_NULL, null=True, blank=True, related_name="authoritative_annotations_set")`
- `authoritative_set_at: DateTimeField(null=True, blank=True)`

DB constraint enforcing at-most-one authoritative annotation per item:

```python
constraints = [
    models.UniqueConstraint(
        fields=["item"],
        condition=models.Q(is_authoritative=True),
        name="one_authoritative_annotation_per_item",
    ),
]
```

Partial unique index — only `is_authoritative=True` rows participate, so multiple non-authoritative annotations on the same item are still allowed.

### `AnnotationItemStatus` — one new value

```python
AWAITING_RESOLUTION = "awaiting_resolution", "Awaiting resolution"
```

Only reachable when `num_reviews_required > 1`. Means: all required reviews submitted, no authoritative annotation chosen yet.

### Migration

Single migration adds the three fields + the partial unique constraint + the new status choice value, plus a data backfill:

- For every item with `num_reviews_required == 1` that has exactly one submitted annotation, set `is_authoritative=True`, `authoritative_set_by=None`, `authoritative_set_at=NOW()` on that annotation. (Status stays `COMPLETED`.)
- For every multi-reviewer item currently at `AnnotationItemStatus.COMPLETED`, downgrade to `AWAITING_RESOLUTION`. (No retroactive authoritative pick — that requires human input.)
- A migration test asserts both branches.

## Status transitions

Rewrite `AnnotationItem.update_status` (`apps/human_annotations/models.py:183`):

```python
def update_status(self, save=True):
    if self.status == AnnotationItemStatus.FLAGGED:
        return

    has_authoritative = self.annotations.filter(
        status=AnnotationStatus.SUBMITTED, is_authoritative=True
    ).exists()
    required = self.queue.num_reviews_required

    if self.review_count == 0:
        new_status = AnnotationItemStatus.PENDING
    elif self.review_count < required:
        new_status = AnnotationItemStatus.IN_PROGRESS
    elif required == 1 or has_authoritative:
        new_status = AnnotationItemStatus.COMPLETED
    else:
        new_status = AnnotationItemStatus.AWAITING_RESOLUTION

    self.status = new_status
    if save:
        self.save(update_fields=["status"])
```

### Auto-mark on single-reviewer queues

In `Annotation.save` (`apps/human_annotations/models.py:226`), when creating a new SUBMITTED annotation, auto-mark only when *both*:

- `self.item.queue.num_reviews_required == 1`, **and**
- no other annotation on the same item already has `is_authoritative=True`.

When both hold, set `is_authoritative=True`, `authoritative_set_by=None`, `authoritative_set_at=timezone.now()` on `self` **before** calling `super().save()` so the values land in the INSERT. After-the-fact (over-budget) submissions from extra assignees don't auto-mark, avoiding the partial-unique-constraint conflict. Admins can still pick a different authoritative annotation via the toggle.

### Admin toggle (multi-reviewer)

The new `SetAuthoritative` endpoint (see UI section), inside `transaction.atomic()` with `AnnotationItem.objects.select_for_update()`:

1. If `value=true`: clear `is_authoritative=False`, `authoritative_set_by=None`, `authoritative_set_at=None` on all other annotations for the same item. Set the chosen annotation's flag/setter/timestamp.
2. If `value=false`: clear flag/setter/timestamp on the chosen annotation.
3. Call `item.update_status()` (drives `AWAITING_RESOLUTION ↔ COMPLETED`).
4. Call `annotation.recompute_queue_aggregates(queue)`.

Admins may pre-mark authoritative before all reviews are submitted; status stays `IN_PROGRESS` and only transitions to `COMPLETED` once review count meets the requirement.

### Edit / flag / unflag

- `EditAnnotation` (`apps/human_annotations/views/annotate_views.py:233`) does **not** clear `is_authoritative` — per the issue, the canonical workflow is "the correct annotation would be edited, with one of them flagged as authoritative." Existing `recompute_queue_aggregates` call already handles aggregate updates.
- `FlagItem` / `UnflagItem` (`annotate_views.py:304` and `:330`) leave `is_authoritative` untouched. `UnflagItem` already calls `update_status`, which now picks the right post-flag status based on the authoritative flag.

## Aggregation ("concordance") change

Modify `apps/human_annotations/aggregation.py:15` (`compute_aggregates_for_queue`) to use per-item authoritative-preferring selection:

```python
for item in items:
    submitted = [a for a in item.annotations.all() if a.status == AnnotationStatus.SUBMITTED]
    authoritative = [a for a in submitted if a.is_authoritative]
    contributing = authoritative if authoritative else submitted
    for ann in contributing:
        for field_name, value in ann.data.items():
            if field_name in aggregatable_fields and value is not None:
                field_values[field_name].append(value)
```

A fully-resolved multi-reviewer item contributes one value per field; an unresolved item contributes all reviewer values. The existing prefetch in `aggregation.py:23` covers the new boolean field without changes.

### Recompute triggers

- `Annotation.save` (existing, on new SUBMITTED rows) — unchanged.
- `EditAnnotation.post` (existing) — unchanged.
- `SetAuthoritative` (new) — calls `recompute_queue_aggregates`.

## Progress

Add one key to `AnnotationQueue.get_progress` (`apps/human_annotations/models.py:97`):

```python
"resolved_items": self.items.filter(status=AnnotationItemStatus.COMPLETED).count(),
```

Because `COMPLETED` is now redefined to require authoritative (multi-reviewer) or single-reviewer-submission, this count is exactly the "resolved" figure from the issue. Existing keys (`reviews_done`, `total_reviews_needed`, `percent`, etc.) keep their current meaning.

## Permissions

Authoritative toggling requires `human_annotations.change_annotationqueue` (existing permission, used by `EditAnnotationQueue`). Reviewers without this permission see the authoritative state read-only.

## URLs and views

New route in `apps/human_annotations/urls.py`:

```python
path(
    "queue/<int:pk>/item/<int:item_pk>/annotation/<int:annotation_pk>/authoritative/",
    annotate_views.SetAuthoritative.as_view(),
    name="set_authoritative",
),
```

New view `SetAuthoritative(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View)` in `apps/human_annotations/views/annotate_views.py`:

- `permission_required = "human_annotations.change_annotationqueue"`
- POST-only.
- Body: `value=true` or `value=false`.
- Validates queue / item / annotation are in the same team scope.
- HTMX-aware: returns the re-rendered annotation list partial when `HX-Request` is present; otherwise redirects back to `annotate_item`.

Modifications to existing views:

- `AnnotateItem.get` (`annotate_views.py:128`): the `annotations` list passed to the template gains `is_authoritative`, `can_set_authoritative` (= user has `change_annotationqueue`), `authoritative_set_by`, `authoritative_set_at` per row.

## UI

### Annotation rows in `annotate.html`

Each row in the existing annotations list gets a small status pill:

- **Authoritative**: solid amber/primary chip with a star glyph and label "Authoritative". Tooltip: "Marked authoritative by `<setter>` on `<timestamp>`" (omits the by-clause when setter is null, e.g. auto-marked single-reviewer rows).
- **Mark authoritative button** (only when `can_set_authoritative`): outlined chip "Mark authoritative". POSTs to the new endpoint with `value=true`.
- **Clear button** (only when `can_set_authoritative` AND the row is currently authoritative): "Clear authoritative" — POSTs `value=false`.

### Awaiting-resolution callouts

- `templates/human_annotations/columns/item_status.html`: render an amber chip for `AWAITING_RESOLUTION` ("Awaiting resolution").
- `templates/human_annotations/annotate.html`: when the current item is `AWAITING_RESOLUTION`, show a soft banner: "All required reviews submitted. An admin must mark one annotation as authoritative to resolve." No action button for non-admins.
- `templates/human_annotations/queue_detail.html`: progress block adds a second line, `{resolved_items} / {total_items} items resolved`. If `awaiting_count > 0`, surface a callout: "N items awaiting resolution" linking to the items table filtered to that status.

### Item-table summary

`templates/human_annotations/columns/annotations_summary.html`: prefix the authoritative annotation's reviewer name with a star glyph so reviewers can see at a glance which row was canonical.

### Status filter

The items-table status dropdown (driven by `AnnotationItemFilter` in `apps/human_annotations/filters.py`) gains the new `AWAITING_RESOLUTION` value via the existing `AnnotationItemStatus.choices` wire-up — no manual filter changes expected, but a test asserts the new option is present.

### Queue table

`apps/human_annotations/tables.py:21` (`AnnotationQueueTable`): unchanged. The new resolved figure lives on the detail page, not the listing.

## Export

`ExportAnnotations` (`apps/human_annotations/views/queue_views.py:502`):

- CSV: add `is_authoritative` to `fieldnames`. Each annotation row writes the bool; flagged-item placeholder rows write the empty string.
- JSONL: each record gains an `is_authoritative` key alongside `annotation`. Flagged-item placeholder records get `False`.

No new query parameter — consumers filter downstream.

## Testing

New file `apps/human_annotations/tests/test_authoritative.py`; additions to existing test files.

### Constraint & state-machine tests (`test_authoritative.py`)

- DB partial unique constraint: two `is_authoritative=True` rows on the same item raise `IntegrityError`; two on different items succeed.
- Single-reviewer queue: first submission auto-sets `is_authoritative=True`, `authoritative_set_by=None`, `authoritative_set_at` populated; item → `COMPLETED`. A second submission from a different assignee on the same item does **not** auto-mark and does not raise.
- Multi-reviewer queue (`num_reviews_required=2`):
  - one submission → `IN_PROGRESS`, no authoritative.
  - second submission → `AWAITING_RESOLUTION`.
  - admin marks one → `COMPLETED`; the other annotation has `is_authoritative=False` and null setter/timestamp.
  - admin switches authoritative → flag flips, prior setter/timestamp cleared, new setter/timestamp on new row, still `COMPLETED`.
  - admin clears authoritative → `AWAITING_RESOLUTION`.
- Editing an authoritative annotation preserves `is_authoritative` and triggers `recompute_queue_aggregates`.
- Flagging an `AWAITING_RESOLUTION` item → `FLAGGED`, authoritative untouched; unflagging returns to `AWAITING_RESOLUTION` (or `COMPLETED` if authoritative still set).
- Admin pre-marks before all reviews are in (multi-reviewer, only 1 submission) → status stays `IN_PROGRESS`; second submission → `COMPLETED`.

### Aggregation tests

- Item with authoritative set → aggregator uses only that annotation.
- Item with no authoritative + multiple submissions → aggregator uses all submitted (fallback).
- Mixed queue (some items resolved, some awaiting) → aggregates combine per-item behaviour correctly.
- Toggling authoritative re-fires `recompute_queue_aggregates` and updates `AnnotationQueueAggregate.aggregates`.

### View tests

- POST `set_authoritative` as queue admin → success, flag set, status recomputed.
- POST as a reviewer without `change_annotationqueue` → 403.
- POST cross-team (item belongs to a different team) → 404.
- POST `value=false` clears the flag.
- HTMX request returns the partial; non-HTMX returns a redirect to `annotate_item`.
- Concurrent toggles on the same item (simulated) — final state has at most one authoritative annotation.

### Migration test

Use Django's `MigratorTestCase` pattern:

- Queue with `num_reviews_required=1` and one submitted annotation pre-migration → annotation is authoritative post-migration.
- Multi-reviewer queue with an item at `COMPLETED` and no authoritative pre-migration → item is `AWAITING_RESOLUTION` post-migration.

### Export tests

- CSV header contains `is_authoritative`; row values match.
- JSONL records include the `is_authoritative` key.

### Progress test

- `get_progress` returns `resolved_items` matching the count of items at `COMPLETED`.
- The `AWAITING_RESOLUTION` value is selectable on the items-table status filter.

## Out of scope (revisit if asked)

- Custom export filters (e.g. authoritative-only).
- Audit log table (every set/clear). Last-setter + timestamp on the annotation is the chosen depth.
- Per-queue setting to opt out of single-reviewer auto-marking.
- Notifications when an item enters `AWAITING_RESOLUTION`.
- Inter-rater agreement / kappa-style metrics.
