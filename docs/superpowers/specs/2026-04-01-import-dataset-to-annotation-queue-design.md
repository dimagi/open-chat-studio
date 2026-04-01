# Design: Import Sessions from Evaluation Dataset into Annotation Queue

**Date:** 2026-04-01
**Branch:** cs-dataset_to_annotation

---

## Overview

Allow users to import sessions from an existing `EvaluationDataset` into an `AnnotationQueue`. Sessions are extracted from the dataset's `EvaluationMessage` records via `metadata["session_id"]` (which holds the `ExperimentSession.external_id`). Duplicate sessions (already present in the queue) are silently skipped.

---

## UI Change: Dropdown on Queue Detail

The flat "Add Sessions" button on `templates/human_annotations/queue_detail.html` is replaced with a DaisyUI `dropdown` component. The dropdown contains two entries:

- **Choose Sessions** — links to the existing `queue_add_sessions` URL (no behaviour change)
- **Import from Dataset** — links to the new `queue_import_from_dataset` URL

Permission gate: the dropdown (and both entries) is only rendered when the user has `human_annotations.add_annotationitem`, matching the existing gate on "Add Sessions".

---

## New View: ImportFromDataset

**URL:** `queue/<int:pk>/import-from-dataset/`
**Name:** `queue_import_from_dataset`
**Permission:** `human_annotations.add_annotationitem`
**Class:** `ImportFromDataset(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View)`

### GET

Renders `human_annotations/import_from_dataset.html` with:
- `queue` — the `AnnotationQueue` instance (scoped to `request.team`)
- `form` — `ImportFromDatasetForm` instance

### POST

1. Validate `ImportFromDatasetForm`.
2. Get all `EvaluationMessage` objects for the selected dataset.
3. Collect unique `session_id` values from `message.metadata.get("session_id")` (skipping blanks/nulls).
4. Resolve external IDs to `ExperimentSession` PKs: `ExperimentSession.objects.filter(external_id__in=session_ids, team=request.team).values_list("id", flat=True)`.
5. Subtract sessions already in the queue: `AnnotationItem.objects.filter(queue=queue).values_list("session_id", flat=True)`.
6. Bulk-create `AnnotationItem` for net-new sessions (`ignore_conflicts=True` as safety net).
7. Flash a success message: `"Added {n} sessions. Skipped {k} already in queue."` (or just `"Added {n} sessions."` if none skipped).
8. Redirect to `queue_detail`.

If the form is invalid, re-render the form with errors. If no sessions are found in the dataset metadata, flash an error and redirect to `queue_detail`.

---

## New Form: ImportFromDatasetForm

```python
class ImportFromDatasetForm(forms.Form):
    dataset = forms.ModelChoiceField(
        queryset=EvaluationDataset.objects.none(),
        label="Dataset",
        help_text="Select the evaluation dataset to import sessions from.",
    )

    def __init__(self, *args, team, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dataset"].queryset = EvaluationDataset.objects.filter(
            team=team, status=DatasetCreationStatus.COMPLETED
        ).order_by("name")
```

Only completed datasets are shown (processing/failed datasets have no messages).

---

## New Template: import_from_dataset.html

Extends `web/app/app_base.html`. Breadcrumbs: Annotation Queues → Queue Name → Import from Dataset.

Contains:
- Page heading with queue name
- A `<form method="post">` with CSRF token
- A `<select>` rendered from `form.dataset` (standard DaisyUI `select select-bordered`)
- A submit button (`btn btn-primary`)
- A cancel link back to `queue_detail`

---

## Data Flow Summary

```
EvaluationDataset
  └── messages (M2M) → EvaluationMessage
        └── metadata["session_id"]  # ExperimentSession.external_id

ImportFromDataset.post()
  1. Collect unique session_id strings from dataset messages
  2. Resolve → ExperimentSession PKs (team-scoped)
  3. Subtract existing AnnotationItem.session_id in queue
  4. bulk_create AnnotationItem for new sessions
```

---

## URL Registration

Add to `apps/human_annotations/urls.py`:

```python
path(
    "queue/<int:pk>/import-from-dataset/",
    queue_views.ImportFromDataset.as_view(),
    name="queue_import_from_dataset",
),
```

---

## Permissions

No new permissions are introduced. The new view reuses `human_annotations.add_annotationitem` (same as "Add Sessions").

---

## Tests

- Form: `ImportFromDatasetForm` only shows completed datasets for the correct team.
- View GET: renders form, 403 without permission.
- View POST happy path: creates correct `AnnotationItem` records, skips duplicates, reports counts.
- View POST empty metadata: flashes error, no items created.
- View POST all duplicates: adds 0, reports skipped count correctly.
