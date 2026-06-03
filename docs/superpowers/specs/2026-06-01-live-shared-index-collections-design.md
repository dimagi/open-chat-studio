---
status: stable
issue: https://github.com/dimagi/open-chat-studio/issues/2123
---

# Live shared index collections

> **Scope note (post-design):** this doc covers the original *index*-collection work.
> Media collections (`collection_id`) were subsequently brought under the same rule;
> the canonical, generalised decision is [ADR-0031](../../adr/0031-collection-content-is-live-shared-resource.md).

## Problem

Document sources update index collections on a schedule, but those updates only
reach the **working** version of a collection. A published chatbot references a
**frozen deep copy** of the collection that was made when the bot was published,
so scheduled updates never reach published bots. This defeats the purpose of the
document-source auto-update feature.

Today, when an experiment is published, `_set_versioned_param_list_values(...,
"collection_index_ids", Collection)` (`apps/pipelines/models.py:423`) replaces
each index-collection id in the node params with the id of a newly created,
frozen `Collection` version. At runtime, `get_collections_for_search`
(`apps/pipelines/repository.py:183`) resolves those frozen ids, and search is
scoped to that version's own content — local search filters
`FileChunkEmbedding` by `collection_id` (`apps/chat/agent/tools.py:101-126`),
remote search uses that version's own `openai_vector_store_id`. The content
lives wherever the node param points, and for published bots that pointer is a
frozen snapshot.

## Principle

An index collection's **content** is a live, shared resource kept current by
document sources. A published chatbot reads the live index by default. Freezing
is **explicit and opt-in** (snapshots), reserved for the rare eval/audit case.

This decouples two lifecycles that the current model conflates:

- The **bot's configuration** lifecycle — frozen at publish (prompt, pipeline
  shape, query-time settings such as `max_results` which live on the node).
- The **index's content** lifecycle — continuously refreshed by document
  sources, shared across every bot that references it.

Index content-defining configuration (embedding model, chunking strategy) is a
property of the shared content store, not of any one bot version, so it does not
need to be versioned per bot.

## Scope

In scope:

- Stop creating frozen index-collection versions when a bot is published.
- An explicit "Snapshot" action to freeze an index on demand, and the ability to
  point a pipeline node at a snapshot instead of the live index.
- A guard ensuring snapshots (and legacy frozen copies) never auto-sync.

Out of scope:

- Media collections referenced via `collection_id` (single-collection message
  attachments) — these keep their current versioning behaviour unchanged.
- Any data migration of existing published bots (see "Migration" — new-only).
- Wiring snapshots into specific evaluation flows; a snapshot is simply a
  selectable, addressable frozen index.

## Design

### 1. Core change — stop versioning indexes at publish

In pipeline node versioning (`apps/pipelines/models.py`, around line 423,
`Node.create_new_version` / its `LLMResponseWithPrompt` branch), **remove the
`_set_versioned_param_list_values(new_version, "collection_index_ids",
Collection)` call**. The published node then keeps whatever ids the working node
holds, verbatim:

- the **live working-collection id** by default, or
- a **snapshot id** if the user explicitly selected one.

`_set_versioned_param_value(new_version, "collection_id", SourceMaterial/...)`
and the single-collection `collection_id` handling are left unchanged.

Consequences (all desirable, all fall out without further code):

- Newly published bots reference the live index. Document-source sync already
  writes the working collection, so **no sync-side changes are needed for
  freshness**.
- **Dirty-state self-corrects.** The version diff recurses into collection
  content (`apps/experiments/versioning.py:114-151`,
  `apps/pipelines/models.py:489-493`). Because published and working nodes now
  hold the *same* working-collection id, the recursion compares the index to
  itself → no diff. Index content/config drift no longer pollutes a bot's
  "unpublished changes" indicator. No comparison-logic change required.
- **Deletion guard already protects the live index.** `Collection.archive()`
  (`apps/documents/models.py:272-292`) blocks on
  `get_related_nodes_queryset()`, which matches
  `params__collection_index_ids__contains=<id>` across *all* nodes including
  published ones (`apps/utils/deletion.py:230-237`). Since the working-collection
  id is now present in published params, archiving a referenced live index is
  blocked automatically. No new guard code required.

### 2. Snapshot feature (explicit freeze)

- **Action.** A "Snapshot" control on a working index collection calls the
  existing `Collection.create_new_version()` (`apps/documents/models.py:174-245`),
  which already deep-copies files, document sources, local
  `FileChunkEmbedding` rows, and — for remote indexes — creates a fresh OpenAI
  vector store. The result is an ordinary frozen `Collection` version, listed
  under the collection's existing `versions` relation.
- **Selection.** The LLM node's index picker offers **"Live index" (default)**
  or a named snapshot of that index. The chosen id is stored in
  `collection_index_ids`. Runtime already resolves any `is_index` collection id,
  so there are no runtime changes — a snapshot has its own embeddings / vector
  store and searches its frozen content.
- Snapshots never auto-update (see §3).

### 3. Sync guard for snapshots

Tighten `sync_all_document_sources_task` (`apps/documents/tasks.py:209`) so it
only targets working collections by adding
`collection__working_version__isnull=True` to the existing filter
(`auto_sync_enabled=True, collection__is_index=True`). This guarantees snapshots
and legacy frozen copies never drift, regardless of the `auto_sync_enabled` flag
that `create_new_version()` copies onto a snapshot's document sources.

### 4. Migration — none (new-only)

Existing published bots keep their frozen index ids and behave exactly as they
do today. They pick up live behaviour the next time they are republished (the
republish keeps the working id per §1). Legacy frozen copies remain referenced
by their published versions — they are not orphaned, so existing version-deletion
paths handle eventual cleanup.

Known, accepted consequence: a not-yet-republished old bot continues to show
"unpublished changes" whenever its working index is synced, until its next
publish. This is the deliberate trade-off of the new-only approach (zero
deploy-time behaviour change for live bots).

### 5. UI copy

- Published versions now read "uses live index 'Foo'".
- Legacy frozen references (and deliberately pinned snapshots) read as pinned to
  a specific snapshot.

## Testing

- Publishing a bot whose LLM node references an index collection does **not**
  create a new `Collection` version, and the published node param retains the
  working-collection id.
- Publishing still versions a media `collection_id` (regression guard for the
  unchanged path).
- A document-source sync to a working index is visible to a bot published
  *after* this change — assert via the resolve + search path
  (`get_collections_for_search` + `_perform_collection_search`).
- The snapshot action produces a frozen copy with its own embeddings / vector
  store; a node pointed at the snapshot id searches the frozen content; the
  snapshot is excluded from `sync_all_document_sources_task`.
- Archiving a live working index referenced by a published bot is blocked
  (existing guard, new assertion to lock the behaviour in).
- After this change, a document-source sync does **not** mark a post-change
  published bot as having unpublished changes.

## Edge notes (documented, not blocking)

- A live index referenced by multiple bots: updates affect all of them. This is
  intended under "live is the source of truth".
- Mid-sync partial index state is pre-existing behaviour on the working
  collection and is unchanged.
- Snapshots accumulate storage (full embeddings copy / separate vector store).
  Deleting a snapshot not referenced by any node is permitted by the existing
  guard; surfacing/managing snapshot cleanup in the UI is a possible follow-up.

## Rejected alternatives

- **Approach A — push syncs into published copies.** Keep deep-copy-on-publish
  and re-embed into every live published copy on each sync. Rejected: re-embeds /
  re-uploads N times per sync (cost, rate limits), and is philosophically
  incoherent — freezing on publish then immediately un-freezing on every sync.
- **Approach B — shared content store, keep per-version records.** Keep a
  `Collection` version record per publish for audit but share the working
  version's content. Rejected: those per-version index records own no content
  (content-defining config must match the shared store; query-time settings live
  on the node), so they duplicate config while carrying nothing meaningful.
- **Notify-only (original option 3).** Never change a published bot
  automatically; surface pending updates and let the user republish. Rejected:
  contradicts "freshness by default", and republishing captures *all* working
  changes, not just the index, so users cannot selectively publish index updates.
