# ADR-0054: A stale in-progress log is the restart ticket

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-08-05</p>

<p class="adr-meta">Extends: <a href="0053-detect-interrupted-sync-with-heartbeat.md">ADR-0053</a></p>

## Context

A sync killed mid-run leaves its `DocumentSourceSyncLog` at `IN_PROGRESS` forever, because the error handler never runs, and nothing re-dispatches it. A source with auto-sync waits up to a week for the next scheduled run; a manual-only source waits indefinitely.

Restarting automatically must not retry a source that is genuinely broken — bad credentials should not be retried every five minutes. That distinction is already in the data and needs nothing recorded to preserve it: a sync that fails for a real reason runs its error handler and closes its log, and only a hard kill leaves one open. [ADR-0053](0053-detect-interrupted-sync-with-heartbeat.md) gives that log a heartbeat, so "open, and no longer reporting" is decidable rather than inferred from elapsed time.

Restarts also have to be bounded. A source whose content reliably exhausts its worker is indistinguishable from one killed by a deploy, so an uncapped restart loop would re-drive it forever and hit an external API on every pass.

## Decision

We will restart a sync whose log is in progress and has stopped reporting, and the restarted run will continue that same log rather than replacing it.

- One beat task selects `DocumentSourceSyncLog` rows with `status = IN_PROGRESS` whose `heartbeat_at` is older than `SYNC_STALE_AFTER`, on sources whose collection still qualifies.
- Below `MAX_RESUME_ATTEMPTS` it increments `DocumentSource.interrupted_sync_attempts`, clears the source's claim, and dispatches a fresh sync. **The log is left open and untouched.** An interruption is not recorded as a failure.
- The restarted run adopts that open log: it heartbeats on it, accumulates its counts onto it, and closes it when it finishes. Any sync that claims a source with an open log continues it, so a user manually re-syncing an interrupted source behaves identically and never opens a second row.
- At the cap the sweep stops restarting and closes the log `FAILED`, naming the interruption count. That is the only circumstance in which a stale log is failed, and it is accurate: we gave up.
- The counter resets to zero whenever a run closes its own log, success or clean failure — both mean the sync reported back.
- It counts interruptions only. A source failing cleanly — a revoked token, an upstream error — never accumulates a count and is never blocked from syncing.
- `auto_sync_enabled` is ignored.

The counter is incremented before the dispatch is published, so a broker failure spends an attempt rather than allowing an unbounded loop. That is the conservative direction for a mechanism whose job is to stop one.

## Consequences

- A deploy that interrupts a sync produces no user-visible failure. The log stays in progress and finishes normally, which is the accurate account — the work completed, just not in one go.
- The log's counters describe the whole sequence, so each run must add to them rather than overwrite them, and `duration_seconds` becomes elapsed time across the sequence including the gap when nothing was running. Anything reading it as time spent working is wrong.
- The sweep never consumes the ticket, so a dispatch lost to a broker outage costs nothing: the log is still open and stale on the next tick.
- Manual-only sources gain automatic recovery, which they have none of today, at roughly ten to fifteen minutes' latency.
- A sync that failed for a real reason closed its own log, so it is never selected — no stored flag is needed to tell the two apart.
- A source that exhausts its worker every run stops after `MAX_RESUME_ATTEMPTS` and stays stopped until a human clears the count, rather than reopening its own budget. That wedged state must be visible and clearable in the UI; a cap with no escape hatch eventually needs a database edit.
- A restart costs one listing call plus the files still outstanding, so an interruption is cheap for sources with a manifest; Confluence re-downloads but still skips writes and embeddings for work already done.
- `has_sync_errors()` still reports an in-progress log whose heartbeat is stale. That is accurate during the window before a restart lands, and it is the only signal that the sweep itself has stopped running.

## Alternatives considered

- **Close the stale log `FAILED` and open a fresh one per run** — rejected: it manufactures a failure for something recovered automatically, so any deploy landing mid-sync fills the history with red rows that blame the source.
- **Report each interruption to Sentry** — rejected: a deploy killing a sync is expected and self-healing; the attempt counter is what surfaces a source that genuinely keeps dying.
- **Derive the cap by counting consecutive interrupted logs** — rejected: nothing can clear a derived streak, so a wedged source stays wedged until an unrelated sync happens to succeed, and the user has nothing to act on.
- **A boolean marking a run as interrupted** — rejected: an interruption is no longer recorded as a failure, so there is nothing to distinguish it from.
- **Count every failure, not just interruptions** — rejected: a source with a revoked token would be blocked from syncing at exactly the moment the credentials are fixed.
- **Restart only sources with auto-sync enabled** — rejected: manual-only sources are precisely the ones with no recovery today.
