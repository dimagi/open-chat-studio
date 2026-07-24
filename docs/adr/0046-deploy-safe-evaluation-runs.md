# ADR-0046: Deploy-safe evaluation runs via a beat coordinator over a frozen plan

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-07-24</p>

<p class="adr-meta">Extends: <a href="0020-delta-evaluation-runs-scoped-to-appended-messages.md">ADR-0020</a></p>

## Context

Evaluation dispatch used a Celery chord: `run_evaluation_task` split the dataset with `.chunks()`, fanned them out, and a `mark_evaluation_complete` callback fired when all chunks finished. Chord state lives only in Redis and is unrecoverable once lost. A deploy (SIGTERM → SIGKILL) mid-run dropped a chunk's remaining messages: the callback never fired, the run stayed `PROCESSING` forever, and that blocked dataset/evaluator/config edits via `InFlightRunsError` with no recovery path. Auto-population ([ADR-0019](0019-poll-source-experiments-to-auto-populate-eval-datasets.md)) compounds this: it grows datasets every ~5 min, while the old task snapshotted `dataset.messages.all()` into an in-memory list only at dispatch. A `FULL` run's scope therefore lived solely in that transient list and the chord, so a crashed run could not be recovered by re-reading the dataset — it has since grown.

## Decision

We will replace chord dispatch with a stateless beat coordinator that derives all run state from the database each tick.

- **Frozen plan at creation.** `EvaluationConfig.run()` snapshots the message plan into `scoped_messages` and the evaluator ids into a new `evaluator_ids` field for *every* run type — FULL = all current dataset ids, PREVIEW = the sample, DELTA = the explicit list ([ADR-0020](0020-delta-evaluation-runs-scoped-to-appended-messages.md)). This extends `scoped_messages`, previously populated only for DELTA, to all types. The coordinator reads only this frozen plan, never the live dataset, so mid-run auto-population cannot change or stall what a run evaluates.
- **Beat coordinator.** `coordinate_evaluation_runs` drives each non-terminal run under `select_for_update(skip_locked=True)` so overlapping sweeps partition runs. Each tick recomputes remaining work from `EvaluationResult` rows, then dispatches the next batch (≤ `BATCHES_PER_TICK`×`BATCH_SIZE` messages), re-dispatches a stalled batch's unfinished messages, or completes the run.
- **Dumb batch tasks.** `evaluate_message_batch` (`acks_late=True`) evaluates a few messages in-process and exits — no callbacks, no self-rescheduling. Redis redelivers a SIGKILLed batch after the visibility timeout.
- **Idempotent and duplicate-proof.** `evaluate_single_message_task` skips evaluators already recorded for a `(run, message)`; a `unique_result_per_run_message_evaluator` constraint plus `IntegrityError` handling makes duplicate rows impossible when a redelivery races a re-dispatch.
- **Completion from the DB.** A run is complete when every planned `(message, evaluator)` pair has a result; 3 consecutive no-progress stalls flip it to `FAILED`, clearing the edit blockage without DB surgery.
- **Progress and observability.** The coordinator publishes done/total to the Celery result backend under `job_id` (polled by the UI) and updates one Taskbadger task per run, created after the tick commits so its HTTP call never holds the row lock.

## Consequences

- A deploy now delays a run by minutes (the next tick repairs it) instead of stranding it forever; per-tick queue depth and LLM load stay bounded.
- A run's truth is its `EvaluationResult` rows, not unrecoverable Redis chord state.
- `scoped_messages` is now non-empty for FULL/PREVIEW, revising ADR-0020's "empty for FULL/PREVIEW"; `run_evaluation_task` no longer branches on type (message selection moved to `EvaluationConfig.run`).
- Coordination fields (`in_flight`, `batch_dispatched_at`, `stall_count`, `evaluator_ids`, `taskbadger_task_id`) are written only by the coordinator under the row lock and saved before any `.delay()`, so a crash under-dispatches (repaired by the stall branch) rather than acting on state that was never persisted.
- Deploy gate: the unique constraint fails to apply if duplicate results already exist, so duplicates must be cleaned first; runs left in-flight by the old code complete on their partial results on the first post-deploy tick.
- Progress granularity and completion latency gain a floor of one beat interval, replacing near-instant chord callbacks — acceptable for a background job.

## Alternatives considered

- **Keep the chord, add a watchdog to resurrect lost callbacks** — rejected: still depends on Redis chord state a deploy can destroy, and the watchdog would reimplement DB-derived completion anyway.
- **Coordinator reads the live dataset for FULL runs** — rejected: auto-population appends rows mid-run, so "remaining" would never reach zero; freezing the plan is what makes completion decidable.
- **Store the frozen plan as a filter or count instead of an explicit M2M** — rejected: it re-resolves against a growing dataset, breaking the stable-scope property (same reasoning as ADR-0020).
- **Per-message Celery tasks instead of small batches** — rejected: one task per message multiplies broker overhead; batching bounds fan-out while keeping redelivery granularity small.
- **One Taskbadger task per batch** — rejected: produces dozens of TB tasks per run; one task per run, updated each tick, is the useful unit.
