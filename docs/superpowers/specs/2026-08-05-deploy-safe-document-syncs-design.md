---

## status: active

# Deploy-safe document source syncs

**In one sentence:** a document source sync killed mid-run — by a deploy, an OOM kill, or a worker recycle — must resume cheaply, get picked back up without a human, and leave no broken state behind.

Today it does none of those things. Files are left permanently un-indexed with nothing to sweep them up, the sync log is stuck reporting "in progress" forever while the UI shows no error, and the next run re-downloads the entire source to discover it has almost nothing to do.

The fix is a separation of responsibilities. Indexing becomes an independent periodic worker whose only job is to find files that need indexing and dispatch that work. The sync task hands indexing over entirely and is left with one job: fetch files from the source and persist them in OCS. Because the two no longer share a fate, killing either one is recoverable.

---

## Background

### What a document source is

A **`Collection`** is a set of files that a chatbot can search. When `is_index` is set, the files are chunked, embedded, and made retrievable — either into a local pgvector index or into a provider-hosted one (`is_remote_index`, e.g. an OpenAI vector store).

A **`DocumentSource`** is an external location a collection pulls files from: a GitHub repository, a Confluence space, or a JSON feed. Users either press "Sync now" or set `auto_sync_enabled` and let a scheduled beat do it. Each source belongs to exactly one collection.

A **`CollectionFile`** is the join row between a `File` and a `Collection`. It carries the two things this document is about:

- `status` — one of `pending`, `in_progress`, `completed`, `failed`, **or blank**. Blank is a real and meaningful state: publishing a collection version copies file rows via the many-to-many, which leaves `status` unset, and `chunk_from_indexed_file()` treats blank as *trusted*. Only the four named statuses mean "this row has been through indexing".
- `document_source` — nullable. Null means a manual upload rather than a synced file.

A **`DocumentSourceSyncLog`** row is opened per sync run with `status = IN_PROGRESS`, and closed as `SUCCESS` or `FAILED` with per-file counts and a duration. It is what the user sees in the source's sync history, and `has_sync_errors()` reads the most recent one to decide whether to show an error.

### What happens today when a source syncs

`sync_document_source_task` takes a lock on the source row — `sync_task_id` plus `sync_started_at` — and calls `DocumentSourceManager.sync_collection()`, which:

1. opens a `DocumentSourceSyncLog` at `IN_PROGRESS`;
2. iterates `loader.load_documents()`, which downloads every file in the source;
3. for each document, compares a version token in the *downloaded* file's metadata against the stored copy, and writes the file if it differs — each in its own transaction, committing as it goes;
4. deletes any previously-synced file whose identifier it did not see;
5. dispatches `index_collection_files_task` **once, at the very end**, for every file it touched;
6. closes the log as `SUCCESS`.

Indexing itself is a separate Celery task. `index_collection_files()` flips its rows to `IN_PROGRESS`, groups them by chunking strategy, uploads and embeds them, and writes `COMPLETED` or `FAILED`. Six call sites dispatch it: the sync above, manual file upload, the retry-failed-uploads button, `migrate_vector_stores`, `create_collection_from_assistant_task`, and `create_new_version`.

The lock self-heals: a lock older than `SYNC_LOCK_TIMEOUT` (two hours) is treated as abandoned. It has to be that long, because a start timestamp tells you when work *began*, not whether it is still going — a lock 90 minutes old is equally consistent with a healthy long sync and with a worker that died 89 minutes ago, and only one of those is safe to assume.

---

## The problems

Everything below follows from one structural fact: **per-file work commits as it goes, but the indexing dispatch happens once at the end.** A run that dies partway through has therefore already written files to the database, and has not told anything to index them.

**1\. Files strand un-indexed, permanently.** Kill the worker at file 900 of 1,000 and 900 `CollectionFile` rows sit at `pending` with nothing dispatched. Nothing in the codebase sweeps `pending` rows. Worse, the next sync won't fix it: those files were written successfully, so their stored version tokens match the source and the diff correctly classifies them as unchanged and skips them. Their content is searchable nowhere, and the only recovery is a human noticing and clicking re-index.

**2\. Nothing triggers recovery, and the UI says everything is fine.** On `SIGKILL` the `except` block never runs, so the log stays `IN_PROGRESS` forever. `has_sync_errors()` only reports a log that is `FAILED` or has `files_failed > 0`, so a stuck log reads as "no errors". The lock expires after two hours, but nothing re-dispatches the sync — a source with auto-sync waits until its next scheduled run, up to a week away, and a manual-only source waits indefinitely.

**3\. Resuming re-downloads the entire source.** Change detection compares `document.metadata["sha"]` — a value that only exists once the file has been downloaded in full. So the sync must fetch all 1,000 files to conclude that 900 of them were already up to date. For GitHub this is pure waste: the sha is already present in the git-tree listing that enumerates the repository, and we throw it away and fetch anyway.

**4\. Stale chunks survive a re-index.** Nothing clears a file's existing chunks when it is re-indexed *successfully*. A file whose content changed therefore gets fresh chunks stacked on top of its old ones, and retrieval sees both. (The retry-failed-uploads path clears chunks explicitly; the normal path does not.)

**5\. A file skipped by a loader gets deleted.** The set of identifiers "seen" this run is built inside the fetch loop, from documents the loader actually *yields*. Anything the loader skips before yielding is never added to that set, so step 4 reads it as "gone from the source" and deletes it. A Confluence page that renders empty, or a JSON attachment whose download returns a 500, silently loses its file. A partial run has the same effect at scale: every file it never reached looks deleted.

---

## The changes

### 1\. A periodic worker owns all indexing

**Why.** Problems 1 and 4 both come from indexing being triggered by whoever happened to write the rows. A dispatch is not durable — it exists only in a Celery message that a deploy can destroy — so every caller needs its own recovery story, and none of them has one. Chunk clearing has the same shape: it is the caller's job today, so it is done on one path and forgotten on the others.

**What.** A new beat task, `coordinate_file_indexing`, becomes the only thing that dispatches indexing. Its sole responsibility is to find files that need indexing and dispatch that work. It is stateless between ticks: every tick recomputes what is outstanding from data already in the schema, with no work-list table and no in-flight bookkeeping.

The existing columns already carry the necessary evidence, and one new column completes it:

| Question | Answered by |
| :---- | :---- |
| What work is outstanding? | `CollectionFile.status` — the work queue |
| What is being worked on? | `status = in_progress` plus a new `indexing_claimed_at` timestamp |
| Did a worker die? | `indexing_claimed_at` older than `INDEX_STALE_AFTER` |
| How many workers are live right now? | Count of rows whose `indexing_claimed_at` is *fresh* |

A row is eligible when it is `pending`, or `in_progress` with a claim that is stale or null, on an unarchived indexed collection, with `index_timeouts` below its cap. Four details in that predicate are each load-bearing:

- **Blank `status` is excluded.** Blank means "trusted" to `chunk_from_indexed_file()`, so sweeping blank rows would re-index every published collection — and, because a claim clears chunks first, briefly empty them.
- **A null `indexing_claimed_at` on an `in_progress` row is eligible.** That is the deploy window: rows left in progress by the currently-deployed code have no claim timestamp, and a naive `<` comparison excludes nulls in SQL and would strand exactly the rows this worker exists to rescue.
- **Archive and index filters are explicit.** `CollectionFile.objects` is a plain manager and inherits none of the archive filtering that `Collection.objects` gets. Without them, the worker would embed and upload files for collections that `delete_collection_task` is concurrently deleting.
- **Version snapshots are deliberately included**, unlike the scheduled sync which excludes them. A remote-index version legitimately holds `pending` rows and must be indexed for the published version to retrieve anything.

Work is claimed **per file**, under `select_for_update(skip_locked=True)`, in a transaction that commits *before* indexing starts — so a claim survives the worker that took it, and the row lock is not held across a multi-minute embedding run. Per file rather than per batch because with contextual retrieval a batch can run for tens of minutes, and the stale-claim timeout only has to exceed the slowest single *file*.

Concurrency is a **computed deficit**: each tick dispatches `MAX_CONCURRENT_INDEX_WORKERS` minus the rows already claimed and fresh, per collection. Dispatching a flat number every tick without subtracting live workers is an uncapped re-drive — we have had a production incident from exactly that shape, in the evaluation-runs coordinator this design is modelled on.

Two smaller things fall out for free:

- **A claim deletes that file's chunks**, scoped to `(collection, file)` so a published version's copies survive. That fixes problem 4 on every path into indexing at once, rather than one caller at a time.
- **`failed` is terminal**, so a permanently bad file does not loop forever; the existing retry button resets it. A separate counter, `index_timeouts`, increments only when a worker claims a row that was *already* `in_progress` — so it counts unrecorded deaths, not indexing failures. Past `MAX_INDEX_TIMEOUTS` the row is failed.

### 2\. The sync task and its log stop dealing with indexing

**Why.** This is the other half of change 1, and it is what makes problem 1 structurally impossible rather than merely repaired after the fact. If the sync's job ends at "the row is written and marked `pending`", then there is no state in which a sync can die and leave work that nothing will pick up.

**What.** All six existing callers of `index_collection_files` are converted to plain status writes: they write their rows at `pending` and return. The sync task is left with one responsibility — fetch files from the source and persist them in OCS.

The invariant this rests on is that `status` is never a *follow-up* write:

- `_create_file` passes `status=PENDING` as a column value in the same INSERT that creates the row;
- `_update_file` writes the file's content and its status inside one transaction.

So a death either rolls back the whole row (no row exists, nothing to strand) or rolls back both the content and the status, leaving the previous version token in place — which means the next diff sees the file as changed and redoes the write. There is no window in which a row exists but is not yet marked as needing indexing.

Two of the six conversions are not mechanical:

- **`migrate_vector_stores`** consumes the return value of `index_collection_files` — the previous remote file ids — and feeds them to `_cleanup_old_vector_store`. It must now collect those ids itself from `File.external_id` *before* writing the rows `pending`, then call the cleanup directly. Deleting the old provider-side objects before re-upload is safe: content lives in S3 and is re-uploaded on a miss.
- **`create_new_version`** does not currently write a status at all — `_version_files` adds rows through the many-to-many, leaving `status` blank, and the remote branch's rows only acquire a status because `index_collection_files` sets `in_progress`. It must now set `status=PENDING` explicitly on the new version's rows, in the remote-index branch only. Without that they stay blank, eligibility excludes them by design, and the version is never indexed. The local-index branch copies chunk rows directly and must keep `status` blank.

**The sync log narrows to match.** It reports files fetched and written — nothing about indexing, which the sync no longer performs and cannot observe. A `SUCCESS` sync whose files are all still `pending` is correct rather than contradictory. That does change what a green sync log means, so indexing gets its own signal: the coordinator records the age of the oldest row awaiting indexing, and the collection view warns when it exceeds `INDEX_BACKLOG_WARN_AFTER`. This is deliberately separate from the existing failed-files badge — "work is not moving" and "work was attempted and failed" have different causes and different remedies. It also matters more than it did before, because after change 1 a dead beat stops all indexing product-wide, and without a backlog signal that outage is silent.

### 3\. Change detection moves from downloaded content to a source listing

**Why.** Problem 3: comparing version tokens found in downloaded content means you must download everything before you can decide you didn't need to. Problem 5 has the same root — the seen-set is built during fetching, so it is only complete if fetching completes.

**What.** A loader may now enumerate its source without fetching content, returning `ManifestEntry` records of `(external_id, version, handle)` — an identifier, an opaque version token, and a loader-private payload used later to fetch that entry. Given that listing, the diff is a pure function over two collections of strings:

```py
for entry in manifest:
    seen.add(entry.external_id)
    stored = existing.get(entry.external_id)        # {external_id: (collection_file_id, version)}
    if stored is None or not loader.is_current(entry.version, stored[1]):
        todo.append(entry)                          # everything else: no fetch, no write
to_delete = [cf_id for ext_id, (cf_id, _) in existing.items() if ext_id not in seen]
```

**This needs no new state.** The stored version token already lives in `File.metadata`, which is populated wholesale from the document's metadata for every file that has ever synced. No migration, no backfill, and the diff works retroactively on data already in production.

Consequences worth calling out:

- **A resume costs one listing call plus the remainder.** Killed at file 900 of 1,000, the next run makes one tree call and 100 fetches instead of 1,000.
- **The seen-set is complete before anything destructive happens.** A partial run can no longer delete live files, and a listing that errors aborts having written and deleted nothing.
- **A failed fetch is a per-file failure, not an absence** — so problem 5's transient download error stops deleting the file.
- **Both sides stream.** Materialising either the listing or the stored side in full is the shape that OOM'd a 2 GB worker in a previous incident, and here it would be self-perpetuating: an OOM before any progress reproduces exactly on resume, burning every restart attempt until the source is parked.

**Not every loader can do this.** GitHub and JSON collection adopt it. Confluence does not, because LangChain's Confluence loader returns page bodies inline with its listing — a body-less listing would mean writing our own API calls, and it trades one batched paginated request for a listing plus one request per changed page. Confluence keeps the existing streaming path, so a resume re-downloads; it still skips every S3 write and every embedding for work already done, and its stranded files are recovered by change 1 without the sync resuming at all.

To stop the two paths drifting, a manifest-capable loader **derives** `load_documents()` from its own manifest, so they cannot disagree about how an identifier is built, and a single `is_current()` predicate defines "does not need fetching" for both. Problem 5's other half is fixed on the streaming path directly, by removing the empty-content `continue` in `confluence.py` so the service's existing empty-content handling runs and the file is counted as failed rather than vanishing.

### 4\. An interrupted sync is detected and continued

**Why.** Problem 2\. Recovery needs to answer one question — "is this sync still running?" — and a start timestamp cannot. That is why `SYNC_LOCK_TIMEOUT` is two hours, and why an interrupted sync blocks its source for that long and is then never restarted.

**What.** `DocumentSourceSyncLog` gains a `heartbeat_at`, set when the log is created and refreshed on elapsed time as the run works. Liveness becomes one query against one table: a source is being synced when it has an `IN_PROGRESS` log whose heartbeat falls within `SYNC_STALE_AFTER` (10 minutes). That replaces `SYNC_LOCK_TIMEOUT` and every consumer of it.

Details that matter:

- **The heartbeat is time-based, not count-based.** One Confluence page can take minutes while GitHub yields a thousand files in seconds, so no "every N documents" rule is safe at both ends.
- **The log is created with its first heartbeat already set**, so there is never an in-progress log with nothing to judge. A heartbeat on the source row instead would be null until the first document is fetched — which sits behind a full listing call — forcing a second signal to cover that stretch.
- **The heartbeat write doubles as the deleted-or-archived-source check.** Scoped to an unarchived source, its affected-row count tells the run whether its source still exists, replacing a `SELECT EXISTS` per document.
- **The sync task becomes the sole claimer.** Today the view claims the lock and dispatches with a pre-generated task id, which is why the task has to recognise a lock bearing its own id. Dropping the pre-claim removes that special case: the view dispatches, and the task claims under one row lock. A double-click dispatches a second task, which finds a live log on arrival and returns.
- `has_sync_errors()` additionally treats an in-progress log with a stale heartbeat as an error, so a stopped sync stops reading as green.

**Restarting it.** A second beat task, `recover_interrupted_syncs`, runs every five minutes and selects in-progress logs whose heartbeat has gone stale. The discriminator is already in the data and needs nothing recorded to preserve it: **a sync that fails for a real reason runs its `except` and closes its log, so only a hard kill leaves one open.** Bad credentials are therefore never retried here.

For each stale log the sweep increments `DocumentSource.interrupted_sync_attempts`, clears the source's claim, and dispatches a fresh sync. **The log is left open and untouched** — an interruption is not recorded as a failure. The restarted run *adopts* that log: it heartbeats on it, adds its counts to it, and closes it when it finishes. That rule belongs to claiming rather than to the sweep, so a user manually re-syncing an interrupted source behaves identically and never leaves two in-progress rows behind.

Because the sweep never consumes the ticket, a dispatch lost to a broker outage costs nothing — the log is still open and stale on the next tick. The counter is incremented *before* dispatch, so a failed publish spends an attempt rather than allowing an unbounded loop.

Restarts are bounded. At `MAX_RESUME_ATTEMPTS` the sweep stops restarting and closes the log `FAILED`, naming the interruption count — the only circumstance in which a stale log is failed, and an accurate one, because we gave up. The count is shown on the source with a control to clear it, so a wedged source does not need a database edit. It resets to zero whenever a run closes its own log, success or clean failure, since both mean the sync reported back — and it counts interruptions only, so a source with a revoked token accumulates nothing and is never blocked from syncing.

Finally, `auto_sync_enabled` is ignored for restarts. It means "don't sync on a schedule", not "don't finish the sync I just asked for", and manual-only sources are precisely the ones with no recovery today.

---

## What this looks like in practice

A sync of a 1,000-file GitHub repository is killed by a deploy at file 900\.

**Today.** 900 `CollectionFile` rows sit at `pending` with nothing dispatched, and their content is searchable nowhere. The log stays `IN_PROGRESS` and the UI reports no error. The source cannot sync at all for two hours. When it eventually does — up to a week later on a schedule, or never if it is manual-only — it re-downloads all 1,000 files, and the 900 stranded rows are correctly identified as unchanged, skipped, and left un-indexed until a human notices and clicks re-index.

**After.** The indexing worker picks up those 900 `pending` rows on its next tick and indexes them, with no sync running at all. Within ten minutes the log's heartbeat goes stale; the next recovery beat increments the attempt counter and dispatches a fresh sync, which adopts the same open log, makes one tree call, finds 900 files current, fetches the remaining 100, and closes the log. The sync history shows one successful sync of 1,000 files — not a failure followed by a success — because the work did complete, just not in one go. Recovery latency is roughly fifteen minutes, the same for manual and scheduled sources.

---

## Phasing

Independently shippable and reviewable. Phases 2 and 3 do not depend on each other.

**Phase 1 — the indexing worker** (changes 1 and 2). Both `CollectionFile` columns and the partial index; the eligibility queryset; the deficit-bounded tick; per-file claims; the timeout cap; all six entry points converted; chunk clearing at claim time; terminal status written by `CollectionFile.id`; the chunking-strategy sort; the backlog signal. Depends on nothing else and carries both production bugs — stranded files and stale chunks.

**Phase 2 — the manifest** (change 3). `ManifestEntry`, `is_current`, the diff; GitHub and JSON adopt it; the generic `should_update_document`; the delete guard on both paths with its manual override; the Confluence empty-page fix. **No migration.**

**Phase 3 — sync deploy safety** (change 4). `heartbeat_at` and progress counters; the liveness rule across its consumers; `interrupted_sync_attempts` with its UI control; `recover_interrupted_syncs`; the unique constraint.

## Migrations

| Change | Phase | Notes |
| :---- | :---- | :---- |
| `CollectionFile.indexing_claimed_at` (nullable datetime) | 1 | Additive |
| `CollectionFile.index_timeouts` (integer, default 0\) | 1 | Additive. Counts claims that never finished, not indexing failures |
| Partial index on `CollectionFile(collection_id, indexing_claimed_at)` where `status IN ('pending','in_progress')` | 1 | The tick scans this table deployment-wide every 15s. Partial because the steady state is **zero** outstanding rows, so the index stays near-empty rather than growing with every file ever indexed |
| `DocumentSourceSyncLog.heartbeat_at` (nullable datetime) | 3 | Additive. Per-run, set at log creation, so it never needs clearing between runs |
| `DocumentSource.interrupted_sync_attempts` (integer, default 0\) | 3 | Additive. Not versioned configuration — exclude from `_get_version_details` and from `audit_fields` |
| `unique_source_file` partial constraint on `CollectionFile(document_source, external_id)` where `external_id != ''` | 3 | **Pre-deploy check required:** fails to apply if duplicates already exist. Find and clean them up first, keeping the newest, and record that in the PR's Migrations section. Manual uploads and versioned copies have a blank `external_id` and are excluded. This is a safety net — the liveness rule is the actual mechanism |

`SYNC_LOCK_TIMEOUT` is removed in phase 3\. Its consumers — `DocumentSource.is_sync_in_progress`, the "already syncing" message in the sync view, the stale-lock exclusion in `sync_all_document_sources_task`, and the sync task's own guard — all move to the heartbeat rule. `sync_started_at` is kept as a claim timestamp for observability but no longer decides anything.

## Testing

The tests that carry the design, rather than an exhaustive list:

- **The diff, with no database.** It is a pure function over dicts, so the whole matrix — new / changed / current / removed, blank tokens on either side, the resume remainder, both sides of the guard's thresholds — runs without `@pytest.mark.django_db`.
- **Two equivalence tests per manifest-capable loader**, which are what stop someone optimising the two paths apart later: the identifiers from `list_manifest()` equal those derived from `load_documents()`, and an entry's manifest version equals `fetch_document(entry).metadata[version_metadata_key]`. The second catches an upstream metadata-key rename silently turning every file into a full re-fetch and re-embed.
- **Eligibility as a single set-equality assertion** over one `CollectionFile` per state — including blank status, null claim, archived collection, and manual upload. Set equality catches a guard that silently stops filtering; separate per-case tests would not.
- **The deficit.** With *k* fresh claims the tick dispatches `MAX_CONCURRENT_INDEX_WORKERS − k`, and zero when *k* ≥ the maximum. This is the mechanism that prevents the incident shape described in change 1\.
- **Claims.** Indexing runs **outside** the claim transaction — holding a row lock across a contextualised embedding run would hold it for minutes and nothing else would notice. One `django_db(transaction=True)` two-thread test proves two workers never claim the same row.
- **The `pending` invariant.** Raise inside `_create_file` and assert *no* `CollectionFile` row exists; raise inside `_update_file` between the content and status writes and assert the stored version token is unchanged.
- **Deletion regressions.** An empty Confluence page keeps its file and is counted as failed rather than deleted; a JSON attachment whose download raises does the same; the guard blocks a scheduled sync and permits a manual one.
- **Restart behaviour.** The sweep leaves the log alone; the restarted run heartbeats on that same row and accumulates counts onto it; a self-closed `FAILED` log is never selected; the outcome is identical for `auto_sync_enabled` true and false.

**Runtime verification** — tests alone are not sufficient here. Run a real GitHub sync locally and confirm the second run makes one tree call and no content fetches. Kill the worker mid-sync on a source with `auto_sync_enabled=False`; confirm the sweep restarts it with no human action, that the restarted run continues the same log and fetches only the remainder, and that the history ends up showing one successful sync. Separately kill an indexing worker and confirm the coordinator re-claims the file with no sync running.

## Limits and accepted trade-offs

- **A first sync is exactly as slow.** There is nothing to skip when everything is new. Sync wall-clock is unchanged generally — this is about restart cost, not parallelism. Fanning the sync itself into per-batch subtasks would build on the manifest and is a separate piece of work.
- **Confluence gets deploy safety, and coordinator-owned indexing, but not fetch-skipping.**
- **Interactive indexing gains up to one beat of latency.** A manual upload waits for the next tick instead of dispatching immediately, so the beat interval is the floor on that feedback.
- **A restarted log's `duration_seconds` includes the gap when nothing was running.** A log now describes a sequence rather than a single run; anything reading duration as time-spent-working is wrong.
- **A deploy costs a mid-index file one `index_timeouts` count**, being indistinguishable from that file killing the worker. Accepted rather than solved with a shutdown handler — the cap is high enough that five interruptions would have to land on the same file.
- **A transient provider error parks healthy files at `failed`** until a human retries. Automatic retry needs a transient/permanent classification the index managers do not make; the failed-files badge is the recovery path.
- **A permanently-failing file never stores a version token**, so it reappears on the to-do list every run. True today too. Completion means "walked the to-do list once", never "the to-do list is empty".
- **The indexing worker is now the only thing that indexes anything**, so a dead beat is a product-wide outage. The backlog signal makes that visible; it does not make it impossible.
- **The delete guard's thresholds are heuristics**, and its override is coarse — a manual sync bypasses it whether or not the user read the message. Recording the bypass is what makes it auditable.
