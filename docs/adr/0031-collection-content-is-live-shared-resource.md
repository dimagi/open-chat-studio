# ADR-0031: Collection content is a live shared resource

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-01</p>

## Context

Collections back two pipeline LLM-node features: a single *media* collection (`collection_id` — files the bot can attach) and one or more *index* collections (`collection_index_ids` — RAG search, `is_index=True`). Collections support working/published versioning. When a bot was published, its pipeline deep-copied every referenced collection into a frozen `Collection` version and repointed the published node at that copy — for indexes, with their own `FileChunkEmbedding` rows (local) or `openai_vector_store_id` (remote).

This froze content per bot. For index collections it actively broke a feature: document sources refresh only the working collection on a schedule, so scheduled updates never reached the frozen copies a published bot reads (issue #2123). Media collections had the same lockstep behaviour — manual edits never reached published bots. The root tension is that a collection's *content* lifecycle (continuously edited or auto-synced) was conflated with the bot's *configuration* lifecycle (frozen at publish) by versioning the two together.

## Decision

We will treat a collection's content as a live, shared resource rather than versioning it per bot. This applies to both media (`collection_id`) and index (`collection_index_ids`) collections.

- Publishing a bot no longer creates a frozen copy of either collection param; the published node keeps the working-collection id verbatim and reads the live collection.
- Freezing is explicit and opt-in: a user creates a snapshot (a frozen `Collection` version via the existing `create_new_version` path) and selects it in the node; publish preserves whichever id is selected.
- Index auto-sync targets working collections only (`collection__working_version__isnull=True`), so snapshots and pre-existing frozen copies never drift. Media collections have no document sources.
- When a pipeline version is archived, a referenced collection is archived only if it is a frozen version (`is_a_version`); the live working collection is never archived.

## Consequences

- Document-source updates reach every bot reading a live index, and manual edits to a media collection reach every bot referencing it — with no per-bot propagation step.
- A collection's content or config changes no longer count toward a bot's "unpublished changes" diff, because the published and working nodes hold the same id.
- A change to a live collection affects every bot referencing it; per-bot isolation is gone unless a snapshot is pinned.
- The existing deletion/archive guard keeps protecting a live collection, since published node params now contain the working-collection id.
- Rollout is new-only: existing published bots keep their frozen ids until republished (and keep showing "unpublished changes" on index sync until then).
- Each snapshot is a full content copy (for an index, its embeddings or a separate vector store).

## Alternatives considered

- Push each change into the frozen published copies — rejected: for indexes this re-embeds every copy per sync, and freezing at publish then immediately propagating is incoherent.
- Keep a per-publish version record for audit but share content — rejected: those records own no content and only duplicate config.
- Notify users to republish instead of auto-updating — rejected: contradicts freshness-by-default, and republishing captures all working changes, not just the collection.
- Backfill-migrate existing published bots onto live collections — rejected: would change live bot behaviour on deploy; chose new-only instead.
- Keep media collections frozen per bot while only indexes go live — rejected: divergent rules for the two collection params add complexity for no benefit, and manual media edits should reach published bots too.
