---
status: stable
relates_to: docs/adr/0031-collection-content-is-live-shared-resource.md
---

# Collection snapshots (freeze primitive)

## Background and motivation

ADR-0031 made collection content a live shared resource: published bots read the
live collection, not a frozen per-bot copy. The ADR named an explicit, opt-in
**snapshot** as the escape hatch for freezing content, but deferred building it.

Discovery (see "Consumption — deferred" below) found that snapshots have **no
concrete consumer demanding them today**. The one latent need is *evaluation
reproducibility*: an `EvaluationRun` freezes the bot version it ran against, but
the bot reads the live index, so re-running an eval after the index changed
yields different results. Nobody has asked for a fix yet.

Rather than build speculative consumption machinery, this slice ships the
**freeze primitive only** — the ability to manually snapshot an index
collection and inspect the result — to make the capability visible and let real
use cases emerge. It deliberately builds nothing that consumes a snapshot.

## Principle

A user can manually freeze a working **index** collection's current content into
a version they can inspect. Reuses the existing `Collection.create_new_version()`
(which already deep-copies files and local embeddings, or creates a remote vector
store) and the existing read-only collection view that already renders
`is_a_version` collections.

## Scope

In scope:

- A "Snapshot" action on the collection detail page for a working index
  collection, creating a frozen `Collection` version asynchronously.
- A list of a collection's existing snapshots on its detail page, each linking
  to the existing read-only view.

Out of scope (explicitly deferred until a real consumer exists):

- Pinning a snapshot in a pipeline LLM node (node-picker changes).
- Evaluation-reproducibility wiring (referencing snapshots from eval configs/runs).
- Media-collection (`collection_id`) snapshots — trivial follow-up once proven.
- Snapshot labels/descriptions, snapshot deletion, and other management UI.

## Design

### Backend

`Collection.create_new_version()` does remote/embedding work and can be slow, so
creation is asynchronous, mirroring the experiment version-creation pattern.

- **Model:** add `create_version_task_id = models.CharField(max_length=128,
  blank=True)` to `Collection`. Additive, backwards-compatible migration.
- **Task:** `async_create_collection_version(collection_id)` — loads the
  collection, runs `create_new_version()` under `current_team(collection.team)`,
  and clears `create_version_task_id` in a `finally` block.
- **Trigger view:** a POST view requiring `documents.change_collection`. It
  refuses if the collection is not a working index collection or a creation task
  is already in flight, otherwise dispatches the task and stores its id on the
  collection.
- **Status view:** a small GET view returning the snapshot button partial, used
  by HTMX polling to swap back to the action button when the task id clears.

### Frontend

On `single_collection_home`, only when the collection is a working version
(`not read_only`) and `collection.is_index`:

- A **Snapshot** button in the existing action-button row. When
  `create_version_task_id` is set it renders a disabled spinner that polls the
  status view every 2s and swaps itself out when creation completes — mirroring
  `templates/experiments/create_version_button.html`.
- A **Snapshots** section listing `collection.versions` (version number, created
  date, file count), each row linking to that version's read-only
  `single_collection_home`.

### Reuse and guards

- Snapshots are already excluded from document-source auto-sync by Plan 1's
  `collection__working_version__isnull=True` filter, so a frozen snapshot never
  drifts.
- The read-only collection view already exists for `is_a_version` collections —
  no new view needed to inspect a snapshot.
- Archiving guards on collections are unchanged.

## Testing

- The trigger view, with the Celery task mocked, dispatches the task and sets
  `create_version_task_id`; the task clears it in `finally`.
- The task actually creates a frozen version: `collection.versions` gains one
  entry whose `is_a_version` is true (local index, providers reused to avoid the
  embedding-model unique-constraint collision).
- The Snapshot button and Snapshots section render only for a working index
  collection — not for a media collection, not for a version (read-only) page.
- The trigger view enforces `documents.change_collection` and refuses when a
  task is already in flight or the collection is not a working index.
- Re-assert that a created snapshot is excluded from `sync_all_document_sources_task`.

## Consumption — deferred (discovery record)

Discovery into how a snapshot would be consumed found two candidate models, with
no current demand for either:

- **Node-pin:** pin a snapshot in the LLM node so a published bot reads frozen
  content. Cheapest, but freezes all consumers (live traffic and evals), which
  is the wrong granularity for reproducibility-only needs.
- **Eval-controlled:** reference snapshots from `EvaluationConfig`/`EvaluationRun`
  so only eval runs see frozen content. Cleaner separation, but needs a runtime
  collection-id override and eval-side UI; storage cost if auto-snapshotting per
  run.

The latent gap that motivates either is eval reproducibility under ADR-0031
(live index ⇒ non-reproducible re-runs). This slice does not address it; if it
becomes a real need, the eval-controlled model is the likelier fit.
