# ADR-0047: Deploy-safe evaluation runs via a beat coordinator over a frozen plan

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-07-24</p>

<p class="adr-meta">Extends: <a href="0020-delta-evaluation-runs-scoped-to-appended-messages.md">ADR-0020</a></p>

## Context

Evaluation dispatch used a Celery chord: `run_evaluation_task` split the dataset with `.chunks()`, fanned them out, and a `mark_evaluation_complete` callback fired when all chunks finished. Chord state lives only in Redis and is unrecoverable once lost. A deploy (SIGTERM → SIGKILL) mid-run dropped a chunk's remaining messages: the callback never fired, the run stayed `PROCESSING` forever, and that blocked dataset/evaluator/config edits via `InFlightRunsError` with no recovery path. Auto-population ([ADR-0019](0019-poll-source-experiments-to-auto-populate-eval-datasets.md)) compounds this: it grows datasets every ~5 min, while the old task snapshotted `dataset.messages.all()` into an in-memory list only at dispatch. A `FULL` run's scope therefore lived solely in that transient list and the chord, so a crashed run could not be recovered by re-reading the dataset — it has since grown.

## Decision

We will replace chord dispatch with a stateless beat coordinator that derives all run state from the database each tick.

- **Frozen plan at creation.** `EvaluationConfig.run()` snapshots the message plan into `scoped_messages` and the evaluator ids into a new `evaluator_ids` field for *every* run type — FULL = all current dataset ids, PREVIEW = the sample, DELTA = the explicit list ([ADR-0020](0020-delta-evaluation-runs-scoped-to-appended-messages.md)). This extends `scoped_messages`, previously populated only for DELTA, to all types. The coordinator reads only this frozen plan, never the live dataset, so mid-run auto-population cannot change or stall what a run evaluates.
- **Beat coordinator.** `coordinate_evaluation_runs` dispatches one `drive_evaluation_run` task per non-terminal run; each drives its run under `select_for_update(skip_locked=True)` so overlapping ticks partition runs. Each tick recomputes remaining work from `EvaluationResult` rows, then dispatches the next batch (≤ `BATCHES_PER_TICK`×`BATCH_SIZE` messages), re-dispatches a stalled batch's unfinished messages, or completes the run.
- **Completion side effects are their own task.** The tick commits only the COMPLETED transition; `finalize_evaluation_run` then computes aggregates and reverses stale tags. Both sweep the whole run, so neither belongs under the row lock, and neither may share the transaction that makes the run terminal (see the amendment below). It is dispatched after the tick's progress publish, because a completing tick is the last one a run gets: a dispatch error ahead of the publish would cost the run its completion signal too, and nothing would retry either. `finalize_evaluation_run` re-checks that the run is COMPLETED, so a dispatch made in error cannot aggregate a partial result set.
- **COMPLETED does not mean finalized.** `finalize_evaluation_run` stamps `EvaluationRun.finalized_at` when it lands; a run that completes with nothing to finalize is stamped by the tick itself. Readers must not treat a COMPLETED run's absent aggregates as "this run produced none" — a run whose results were all errors legitimately has none, and only `finalized_at` separates the two. The results page waits on it (bounded by `FINALIZATION_GRACE`, because nothing retries a lost finalization) rather than rendering an empty section — but only while it has nothing to show. Aggregates it can see outrank the marker, so a finalization killed between computing them and stamping, or an old-code worker mid-deploy that never stamps at all, does not hide real results behind the wait.
- **Dumb batch tasks.** `evaluate_message_batch` (`acks_late=True`) evaluates a few messages in-process and exits — no callbacks, no self-rescheduling. Redis redelivers a SIGKILLed batch after the visibility timeout.
- **Idempotent and duplicate-proof.** `evaluate_message` skips evaluators already recorded for a `(run, message)`; a `unique_result_per_run_message_evaluator` constraint plus `IntegrityError` handling makes duplicate rows impossible when a redelivery races a re-dispatch.
- **Completion from the DB.** A run is complete when every planned `(message, evaluator)` pair has a result; 3 consecutive no-progress stalls flip it to `FAILED`, clearing the edit blockage without DB surgery.
- **Progress and observability.** The coordinator publishes done/total to the Celery result backend under `job_id` (polled by the UI) and updates one Taskbadger task per run, created after the tick commits so its HTTP call never holds the row lock.

## Consequences

- A deploy now delays a run by minutes (the next tick repairs it) instead of stranding it forever; per-tick queue depth and LLM load stay bounded.
- A run's truth is its `EvaluationResult` rows, not unrecoverable Redis chord state.
- `scoped_messages` is now non-empty for FULL/PREVIEW, revising ADR-0020's "empty for FULL/PREVIEW"; `run_evaluation_task` no longer branches on type (message selection moved to `EvaluationConfig.run`).
- Coordination fields (`in_flight`, `batch_dispatched_at`, `stall_count`, `evaluator_ids`) are written only by the coordinator under the row lock and saved before any `.delay()`, so a crash under-dispatches (repaired by the stall branch) rather than acting on state that was never persisted. `taskbadger_task_id` is the exception, written after the tick commits per the decision above: the run's `PENDING` → `PROCESSING` transition is the under-lock claim on creating that task, so exactly one tick ever creates one. The create is therefore never retried — a failed one costs the run its monitoring task, and every Taskbadger call site no-ops on an empty id.
- Deploy gate: the unique constraint fails to apply if duplicate results already exist, so duplicates must be cleaned first; runs left in-flight by the old code complete on their partial results on the first post-deploy tick.
- Progress granularity and completion latency gain a floor of one beat interval, replacing near-instant chord callbacks — acceptable for a background job.

## Amendment: completion must commit before its side effects (2026-07-30)

As first written, the tick called `_finalize_complete`, which did `mark_complete()` **and** both completion sweeps inside the tick's `transaction.atomic()`. That coupling turned one oversized run into a worker restart loop in production.

A FULL run over a 3548-message dataset finished its last message; every subsequent tick recomputed `remaining` as empty and re-entered the completion path, whose aggregate and tag sweeps loaded the entire run into memory and exhausted the 2 GB celery worker. The SIGKILL rolled the transaction back, so the run returned to `PROCESSING` with its results intact — and the next tick, 30 seconds later, did exactly the same thing. Because the sweep drove runs inline in `created_at` order and this was the oldest run, no run behind it was ever driven: 25 runs sat `PENDING` for hours and their Taskbadger tasks went stale.

Two invariants come out of that:

- **A terminal transition commits on its own.** Whatever the side effects do, the run must leave `NON_TERMINAL_RUN_STATUSES` and stay out. Work that can fail must not be able to rewind the status that stops it being retried. `finalize_evaluation_run` is idempotent so a retry after a partial pass converges, and a permanent failure costs the run its aggregates rather than costing the worker its life.
- **One run's tick cannot be another run's fate.** Per-run `drive_evaluation_run` tasks replace the inline loop, because the inline loop's `try`/`except` could not catch an OOM kill — the signal takes the whole sweep with it.

Both completion sweeps also now stream (`.iterator()`) and retain only what they aggregate, so their memory is bounded by distinct result fields rather than by run size.

The trade this accepts: a crash in the window between the tick's commit and `finalize_evaluation_run.delay` leaves a COMPLETED run unfinalized for good, where the old coupling would have retried it. The dispatch is deliberately *last* after commit, behind the progress publish and the Taskbadger update (see the decision above), so the window holds a result-backend write and a blocking Taskbadger HTTP call — wider than a bare `.delay()`, and no sweep repairs a run stranded in it. `finalized_at` makes such a run identifiable — that is what the results page keys off, and what a repair sweep would key off — but no sweep claims them yet, so the gap is visible rather than closed.

## Alternatives considered

- **Keep the chord, add a watchdog to resurrect lost callbacks** — rejected: still depends on Redis chord state a deploy can destroy, and the watchdog would reimplement DB-derived completion anyway.
- **Coordinator reads the live dataset for FULL runs** — rejected: auto-population appends rows mid-run, so "remaining" would never reach zero; freezing the plan is what makes completion decidable.
- **Store the frozen plan as a filter or count instead of an explicit M2M** — rejected: it re-resolves against a growing dataset, breaking the stable-scope property (same reasoning as ADR-0020).
- **Per-message Celery tasks instead of small batches** — rejected: one task per message multiplies broker overhead; batching bounds fan-out while keeping redelivery granularity small.
- **One Taskbadger task per batch** — rejected: produces dozens of TB tasks per run; one task per run, updated each tick, is the useful unit.
