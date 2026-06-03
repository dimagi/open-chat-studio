# ADR-0031: Index collection content is a live shared resource

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-01</p>

## Context

Index collections (`is_index=True`) feed RAG search in pipeline LLM nodes through the `collection_index_ids` node parameter. Collections support working/published versioning. When a bot is published, its pipeline deep-copies each referenced index into a frozen `Collection` version and rewrites the published node to point at that copy, with its own `FileChunkEmbedding` rows (local) or `openai_vector_store_id` (remote).

Document sources refresh only the working collection on a schedule, so scheduled updates never reach the frozen copies a published bot reads — defeating the auto-update feature (issue #2123). The underlying tension: a RAG index's content wants to stay current (freshness), while a published bot reading a frozen snapshot is reproducible (stability). The two lifecycles — the bot's configuration and the index's content — were conflated by versioning them together.

## Decision

We will treat an index collection's content as a live, shared resource rather than versioning it per bot.

- Publishing a bot no longer creates a frozen copy of index collections; the published node keeps the working-collection id in `collection_index_ids` verbatim. Single media collections referenced via `collection_id` keep their existing per-bot versioning.
- Freezing an index is explicit and opt-in: a user creates a snapshot (a frozen `Collection` version, via the existing `create_new_version` path) and selects it in the node; publish preserves whichever id is selected.
- Auto-sync targets working collections only (`collection__working_version__isnull=True`), so snapshots and pre-existing frozen copies never drift.

## Consequences

- Document-source updates reach every bot reading the live index, with no sync-side change.
- An index's content or config changes no longer count toward a bot's "unpublished changes" diff, because published and working nodes hold the same id.
- The existing archive guard keeps protecting a live index, since published node params now contain the working-collection id.
- A change to a live index affects every bot referencing it; per-bot isolation is gone unless a snapshot is pinned.
- Rollout is new-only: existing published bots stay frozen until republished, and keep showing "unpublished changes" on sync until then.
- Each snapshot is a full content copy (embeddings or a separate vector store).

## Alternatives considered

- Push each sync into the frozen published copies — rejected: re-embeds every copy per sync, and freezing at publish then unfreezing on every sync is incoherent.
- Keep a per-publish version record for audit but share content — rejected: those records own no content and only duplicate config.
- Notify users to republish instead of auto-updating — rejected: contradicts freshness-by-default, and republishing captures all working changes, not just the index.
- Backfill-migrate existing published bots onto the live index — rejected: would change live bot behavior on deploy; chose new-only instead.
