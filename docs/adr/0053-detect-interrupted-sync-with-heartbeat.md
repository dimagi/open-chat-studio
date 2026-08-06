# ADR-0053: Detect an interrupted sync from its log's heartbeat

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-08-05</p>

## Context

A `DocumentSource` is a configured external location — a GitHub repository, a Confluence space, a JSON feed — that Open Chat Studio pulls files from into a collection. The pull runs as a background task, and only one may run per source at a time — two at once would race each other's writes.

Exclusion was enforced with a lock stored on the source row: `sync_task_id` recorded which task held it, and `sync_started_at` when it was taken. A worker that dies — deploy, out-of-memory kill, machine recycle — never releases its lock, so a lock older than `SYNC_LOCK_TIMEOUT` (two hours) was also treated as expired. Until that expiry the source could not sync at all.

That timeout had to be long, because a start time is evidence of when work *began*, not of whether it is still going. A lock 90 minutes old is equally consistent with a healthy long sync and with a worker that died 89 minutes ago, and only one of those is safe to assume. So the price of never killing a slow sync was that an interrupted one blocked its source for two hours.

Every run already has a record of its own: a `DocumentSourceSyncLog`, opened when the run starts and closed when it ends, carrying the run's status and per-file counts.

## Decision

We will judge whether a sync is still running from a heartbeat on that run's own log.

- `DocumentSourceSyncLog.heartbeat_at` is set when the log is created, and refreshed on a fixed time interval — not every N documents — as the run works through the source, alongside the run's progress counters.
- A source is being synced when it has an `IN_PROGRESS` log whose `heartbeat_at` falls within `SYNC_STALE_AFTER`. That one question replaces `SYNC_LOCK_TIMEOUT` and every consumer of it.
- `has_sync_errors()` treats an in-progress log whose heartbeat has gone stale as an error, so a sync that stopped reporting surfaces rather than sitting green.
- The sync task is the sole claimer: it takes `sync_task_id` and opens its log under a single row lock on the source, rather than the request handler claiming and dispatching afterwards. `sync_started_at` remains as a claim timestamp for observability but no longer decides liveness.
- The heartbeat write is scoped to an unarchived source, so its affected-row count tells the run whether its source has since been deleted or archived.
- A partial unique constraint `unique_source_file` on `CollectionFile(document_source, external_id)` where `external_id != ''` catches any duplicate that still slips through.

Because the log is created with its first heartbeat already set, there is never an in-progress log with nothing to judge. A heartbeat kept on the source instead would be null until the first document is fetched — which sits behind a full listing call — forcing a second signal to cover that stretch.

## Consequences

- An interrupted sync is detectable in ten minutes rather than two hours, and the window no longer has to accommodate the slowest legitimate sync.
- Liveness is one query against one table, and it is the same query the recovery sweep selects on.
- Heartbeat state is per-run and never needs clearing; a closed log's heartbeat is simply the last time that run reported.
- A sync that goes quiet for longer than the window is treated as dead while still alive. That is wasteful rather than incorrect: the change-detection diff skips work already done, and the unique constraint rejects a duplicate row.
- The heartbeat interval must stay well below the stale window, which is now a stated invariant rather than an assumption.
- A deleted or archived source is noticed at the next heartbeat rather than the next document, replacing a per-document existence query.
- Requesting a sync no longer takes the lock, so a second request made before the first task starts dispatches a second task; the loser finds a live log on arrival and returns without syncing.
- Deploying the constraint requires cleaning up any pre-existing duplicate rows first.

## Alternatives considered

- **Keep the start timestamp alone, with a shorter timeout** — rejected: that caps how long a sync may legitimately run, so a slow but healthy sync would be killed and restarted.
- **Put the heartbeat on `DocumentSource`** — rejected: it is per-run state, so it has to be actively cleared between runs, and it is null until the first document is fetched, which forces a fallback signal to cover the gap.
- **Keep the lock claim in the request handler** — rejected: the task then has to recognise and adopt a lock taken on its own behalf, which needs a second mechanism to tell that case from a live sync.
- **Heartbeat every N documents rather than every N seconds** — rejected: one Confluence page can take minutes while GitHub yields a thousand files in seconds, so no single count is safe at both ends.
