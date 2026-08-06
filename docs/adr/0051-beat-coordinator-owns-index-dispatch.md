# ADR-0051: Beat coordinator owns all index dispatch

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-08-05</p>

<p class="adr-meta">Extends: <a href="0047-deploy-safe-evaluation-runs.md">ADR-0047</a></p>

## Context

Indexing was dispatched inline by six call sites, the most consequential being the end of a document-source sync. Per-file work commits as it goes; the dispatch does not. A worker killed mid-sync therefore leaves `CollectionFile` rows at `PENDING` with nothing dispatched, and the next sync sees those files as unchanged and skips them. No sweeper for `PENDING` exists, so the only recovery is a human clicking re-index. Separately, nothing clears a file's chunks on a *successful* re-index, so changed content stacks fresh chunks on stale ones.

[ADR-0047](0047-deploy-safe-evaluation-runs.md) established a beat coordinator for evaluation runs against the same class of failure: state that only exists in a dispatch a deploy can destroy.

`DocumentSourceSyncLog` reported on indexing as well as fetching, which was only possible because the sync dispatched the indexing and could observe its outcome.

## Decision

We will make `coordinate_file_indexing` the only thing that dispatches indexing work. Every existing caller of `index_collection_files` writes its rows at `PENDING` and returns.

- **Eligibility is recomputed each tick** from `CollectionFile`: `PENDING`, or `IN_PROGRESS` whose `indexing_claimed_at` is stale or null, with `index_timeouts` below `MAX_INDEX_TIMEOUTS`. Blank `status` is excluded, because version creation uses blank to mean "trusted". Archived and non-index collections are excluded explicitly, since `CollectionFile`'s manager does no archive filtering. Snapshot collections are deliberately included, unlike the scheduled sync ([ADR-0031](0031-collection-content-is-live-shared-resource.md)), because a remote-index version legitimately holds `PENDING` rows and must be indexed for the published version to retrieve anything; the chunk delete being scoped to `(collection, file)` is what makes that safe.
- **Claims are per file**, under `select_for_update(skip_locked=True)`, in a transaction that commits before indexing begins.
- **Per-collection concurrency is a computed deficit**: `MAX_CONCURRENT_INDEX_WORKERS` minus the rows already claimed and fresh.
- **A claim deletes that file's `FileChunkEmbedding` rows**, scoped to `(collection, file)` so a published version's copies survive.
- **`FAILED` is terminal**; `index_timeouts` increments only when a claimed row was already `IN_PROGRESS`, so it bounds unrecorded deaths rather than counting failures.

Indexing reports on itself, and the sync log stops speaking for it.

- `has_sync_errors()` covers files fetched and written, nothing more. A `SUCCESS` sync whose files are all `PENDING` is correct, not a contradiction.
- The coordinator records the age of the oldest row awaiting indexing, and the collection view warns once that age exceeds `INDEX_BACKLOG_WARN_AFTER`. That signal covers rows awaiting or mid-indexing; `FAILED` rows remain the failed-files surface.

## Consequences

- Files stranded by a killed worker are recovered without a human and without a subsequent sync.
- Stale chunks can no longer survive a re-index, on any path into indexing.
- Interactive dispatch gains up to one beat of latency, so the beat interval is the floor on manual-upload feedback.
- Indexing gains a single point of failure: a dead beat stops all indexing product-wide. The backlog signal is what makes that visible rather than silent.
- "Work is not moving" and "work was attempted and did not succeed" are reported on separate surfaces, so diagnosing a collection means checking both.
- Users see a sync report success while files are still pending. That is accurate, but it changes what a green sync log implies.
- `create_new_version` stops uploading a remote-index version's files synchronously, so publishing returns immediately and a fresh version briefly holds `PENDING` files. It must set `status=PENDING` explicitly, because adding files through the many-to-many leaves `status` blank.
- `migrate_vector_stores` must collect the previous remote file ids itself before writing `PENDING`, since it no longer receives them from the indexing call.
- Terminal status must be written per `CollectionFile` row rather than per file id; an unscoped write can mark another collection's row `COMPLETED` and strand it permanently.
- `FAILED` also absorbs transient provider errors, so the failed-files retry button is the only recovery for those.
- The backlog threshold is a heuristic; a large but legitimate backlog will trip it.

## Alternatives considered

- **Keep inline dispatch, add a repair sweep alongside it** — rejected: two writers of indexing decisions, with chunk clearing and timeout accounting duplicated across both.
- **Batch-level claims** — rejected: contextual retrieval makes a batch run for tens of minutes, forcing the stale-claim timeout above the slowest batch rather than the slowest file.
- **An explicit in-flight counter per collection** — rejected: fresh claims are already the evidence, and a counter is state that can leak.
- **Dispatch a flat worker count per tick** — rejected: without subtracting live workers, concurrency grows without bound, which is the failure recorded in ADR-0047.
- **Have `has_sync_errors()` also report files stuck pending** — rejected: conflates fetch errors with index backlog in one boolean, and reattaches the sync log to work it no longer performs.
- **Leave indexing unreported** — rejected: a dead beat would then be a silent, product-wide outage with every sync log still green.
- **Fold the backlog into the failed-files badge** — rejected: not-yet-started and failed are different conditions with different remedies.
