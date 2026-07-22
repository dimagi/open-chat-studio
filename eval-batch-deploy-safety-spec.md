# Spec: Deploy-safe evaluation runs (beat-coordinated waves)

## Problem

Evaluation runs dispatch ~10 large Celery chunk tasks (`run_evaluation_task`, `apps/evaluations/tasks.py`). Chunks ack early and run for minutes to hours. When a deploy kills a worker mid-chunk (SIGTERM then SIGKILL), the chunk's remaining messages are silently dropped, the chord callback never fires, and the `EvaluationRun` stays `PROCESSING` forever — which also blocks dataset/evaluator/config edits via `InFlightRunsError`. There is no recovery path.

Two constraints on any fix:

- **Queue starvation.** All tasks share one FIFO Redis queue. Enqueuing a 700-message run as ~234 small tasks up front starves everything behind them (including chat handling) for 10+ minutes.
- **LLM concurrency.** The old 10-chunk cap doubled as a throttle. Unbounded fan-out lets one run fill all ~50 worker threads with LLM calls.

## Goal

A deploy delays a run by minutes instead of bricking it — no manual fixes, no duplicated results, queue depth and LLM load bounded (≈ the old cap of 10), and completion derived from the database rather than Celery chord state (which lives in Redis and is unrecoverable when lost).

## Design overview

A beat task runs every 30s and coordinates all active runs. Each run's plan (messages + evaluators) is frozen at creation. The sweep dispatches work in waves of small batches, detects stalls from the `EvaluationResult` table, re-dispatches dead work, and completes runs. Batch tasks are dumb: evaluate, write results, exit. No chords, callbacks, or self-rescheduling — any lost piece is repaired by the next tick.

```
beat (30s) → coordinate_evaluation_runs
               ├─ PENDING run?     → dispatch wave 1, mark PROCESSING
               ├─ wave complete?   → dispatch next wave (≤10 batches × 3 messages)
               ├─ progress fresh?  → do nothing this tick
               ├─ stalled?         → re-dispatch only the unfinished messages
               └─ nothing remains? → mark complete (aggregates, tag reversal)
```

## Changes

### 1. Freeze the run plan at creation

In `EvaluationConfig.run()` (`apps/evaluations/models.py:487`), in one `transaction.atomic()`: populate `run.scoped_messages` (existing M2M, today DELTA-only) for every run type — FULL = all dataset message ids, PREVIEW = the sample, DELTA = the explicit list — and snapshot `run.evaluator_ids` (new JSON field) from the config.

(Today the plan is *implicitly* frozen: `run_evaluation_task` reads the message set and evaluator ids exactly once at dispatch and bakes them into the fanned-out task args, so nothing re-reads them afterwards. The sweep breaks that — it re-asks "what's left?" every 30s for the run's whole lifetime, so if it read the dataset directly, the answer could change between ticks. This change makes the freezing explicit instead of a side effect of one-shot dispatch.)

The sweep only ever looks at `run.scoped_messages`, never the dataset. This matters because `auto_populate_eval_datasets` grows datasets every 5 minutes; a FULL run that re-derived its message set each tick might never finish. New messages get evaluated as they do today: a fresh DELTA run. The M2M beats a JSON id list because "what's remaining" stays one indexed query, and hard-deleted messages cascade out of the plan so the run converges.

Delete the type-specific selection in `run_evaluation_task` (`tasks.py:261-266`). PR behavior note: a PREVIEW's sample is now picked at creation time instead of dispatch time (milliseconds apart; creation-time is the more defensible semantics).

### 2. Coordination state on `EvaluationRun`

```python
in_flight = SanitizedJSONField(default=list)      # message ids of the current wave
wave_dispatched_at = models.DateTimeField(null=True, blank=True)
evaluator_ids = SanitizedJSONField(default=list)  # snapshot from change 1
```

(Today the run carries no coordination state at all — the only tracking field is `job_id`, the chord's group id, used purely for the progress bar. All "which work is outstanding" accounting lives inside Celery's chord bookkeeping in Redis. These fields exist because the sweep is stateless between ticks and needs a persisted record of what it last dispatched.)

Written only by the sweep, under the row lock, saved **before** the `.delay()` calls — a crash mid-dispatch then under-dispatches (the stall branch repairs it) rather than dispatching tasks the sweep doesn't know about. Batch tasks never touch these fields.

### 3. The sweep: `coordinate_evaluation_runs` (beat, every 30s)

(Today there is no coordinator: `run_evaluation_task` fans out all work in one shot and a Celery chord — broker-side completion counting in Redis — fires `mark_evaluation_complete` when every chunk reports in. Nothing monitors runs afterwards: a lost chunk, lost callback, or lost dispatcher leaves the run `PROCESSING`/`PENDING` forever. The sweep replaces the chord as the completion mechanism *and* adds the monitoring that currently doesn't exist.)

Beat tasks at 10s and 60s already exist here, so 30s is idiomatic. Per active run, under `select_for_update(skip_locked=True)` so overlapping sweeps split the runs instead of double-driving them:

```python
remaining = run.scoped_messages.exclude(id__in=<messages with a result from
                                        every evaluator in run.evaluator_ids>)
if not remaining:
    complete(run)          # guarded UPDATE ... WHERE status='processing';
                           # winner runs aggregates + reverse_stale_tags
elif run.status == PENDING or wave_done(run):
    wave = remaining[: WINDOW * BATCH_SIZE]          # WINDOW=10, BATCH_SIZE=3
    run.in_flight = wave; run.wave_dispatched_at = now(); run.save()
    for batch in chunked(wave, BATCH_SIZE):
        evaluate_message_batch.delay(run.id, batch)
elif stalled(run):
    redispatch(run, unfinished(run.in_flight))       # also resets wave_dispatched_at
# else: wave in progress, fresh results → no-op
```

- `wave_done`: every message in `in_flight` has a result from every evaluator (one aggregation query).
- `stalled`: `now - max(newest result created_at, wave_dispatched_at) > STALL_TIMEOUT`. The `wave_dispatched_at` floor stops a fresh wave (zero results yet) from looking stalled; resetting it on re-dispatch prevents a hot loop.
- A half-done wave with fresh results — the common case — hits the free no-op branch.
- `run_evaluation_task` shrinks to a fast-path that dispatches wave 1 immediately (users don't wait a tick); the sweep's PENDING branch is its backstop.
- A run that stalls 3 consecutive times with zero new results is marked FAILED with an error message, which clears the `InFlightRunsError` blockage without DB surgery.

### 4. Dumb batch task

```python
@shared_task(base=TaskbadgerTask, acks_late=True, soft_time_limit=240)
def evaluate_message_batch(evaluation_run_id, message_ids):
    run = ...  # gone or no longer PROCESSING → log + return
    for message_id in message_ids:
        evaluate_single_message_task(evaluation_run_id, run.evaluator_ids, message_id)
```

(Today the equivalent unit is a `.chunks()` task covering ~1/10th of the dataset — potentially hundreds of messages, running for hours, acked the moment it starts. `acks_late` can't even be applied to it, because `.chunks()` queues Celery's built-in wrapper task, which ignores the inner task's options. Replacing chunks with a task we own is what makes both the small size and the ack behavior possible.)

- No refill or completion check — coordination lives in the sweep.
- `acks_late=True` (task is acknowledged only after finishing, so the broker knows if it died): a SIGKILLed batch is redelivered by Redis after the visibility timeout (how long Redis waits before assuming an unacked task is dead — 5 min in prod). Recovery in ≤5 min instead of the stall timeout. An optimization, not load-bearing: the sweep survives even acks-early losses.
- `soft_time_limit=240`: best-effort bound (not enforced on the threads pool) under the visibility timeout, to limit overlapping deliveries.

### 5. Idempotent message evaluation

(Today this task unconditionally runs bot generation and every evaluator and creates result rows — it is never re-executed, so it never needed to be safe to re-execute. The new design deliberately re-runs work — broker redelivery and stall re-dispatch — so every re-execution must be a cheap no-op for work already done.)

`evaluate_single_message_task` first drops evaluators that already have an `EvaluationResult` for `(run, message)`; if none remain it returns before bot generation. Re-run work only does what's missing. Intentional consequences: error results (`{"error": ...}`) count as done (matches current no-retry behavior); a partially evaluated message re-runs bot generation, so remaining evaluators judge a fresh bot response (rare crash-path artifact; document in code).

### 6. Unique constraint on `EvaluationResult(run, message, evaluator)`

(Today nothing prevents duplicate result rows — uniqueness has simply never been violated because each `(run, message, evaluator)` is executed exactly once. Once re-execution is a designed-in behavior, the skip check alone has a race window, so the DB must enforce what dispatch used to guarantee.)

Schema-only migration plus `IntegrityError` handling at both `create()` sites ("the other delivery won"). Backstop for the window where a redelivery and a stall re-dispatch race past the skip check.

**Pre-deploy risk:** existing duplicate rows in prod would fail the migration. Run a duplicate check (and cleanup) first.

### 7. Progress UI

(Today the run page polls `web:celery_group_status` with `run.job_id` = the chord's group id; progress is Celery's count of finished chord members. No chord → nothing to poll.)

New flow — **the sweep writes progress to Redis; the frontend polls Redis; no DB reads per poll**:

1. At run creation, set `run.job_id = uuid4()` — repurposed as the progress key.
2. Each tick, the sweep publishes the `done`/`total` it already computed for its wave logic, in celery_progress's meta shape:
   ```python
   app.backend.store_result(run.job_id, {"current": done, "total": total, "percent": ...}, "PROGRESS")
   ```
   On completion, the same call with `"SUCCESS"` and `current=total` — completes the bar and stops the client polling.
3. Frontend: in the run page, replace the group-status component with the celery_progress component `dataset_edit.html` already uses (`celery_progress.js` + its polling URL, passed `run.job_id`).

Why this shape:

- Progress values must come from the DB (the sweep's result counts). Counting Celery task states can't work: stall re-dispatch does the work under *new* task ids, so a lifecycle counter would undercount forever after any recovery.
- The sweep-publishes/Redis-serves split makes each poll an O(1) Redis GET regardless of viewer count.
- `ProgressRecorder` can't be used directly — it writes to its own task's id, and the sweep's id changes every tick — hence the raw `store_result` with the same meta shape.

Accepted: progress moves in 30s steps (today's bar has 10 steps total, so no downgrade); "waiting…" for ≤1 tick before the first write; entries expire after 1 day (`result_expires`), harmless since finished runs render the results table.

## Tuning

| Knob | Value | Constraint |
|---|---|---|
| `BATCH_SIZE` | 3 | keeps batch runtime well under the 5-min visibility timeout and the 120s deploy grace |
| `WINDOW` | 10 batches | caps queue depth and concurrent eval LLM load (matches old chunk cap) |
| tick | 30s | wave-boundary idle tail ≈ tick/2 per wave; ~24 waves on 700 msgs ≈ +6 min on a multi-hour run |
| `STALL_TIMEOUT` | 10–15 min | must exceed worst-case batch runtime + 5-min redelivery window; too tight wastes LLM spend on duplicates (safe but paid), too loose delays rescue by minutes |
| fail-after | 3 stalls, no progress | flips run to FAILED instead of retrying forever |

## Failure modes

| Failure | What happens |
|---|---|
| Worker killed mid-batch (deploy) | Redis redelivers the batch in ≤5 min; skip check resumes where it died |
| Batch broker message lost outright | Stall branch re-dispatches the unfinished messages |
| `run_evaluation_task` dispatcher lost | Sweep picks up the PENDING run next tick |
| Sweep task itself lost | Next tick does the work; ticks are independent |
| Two sweeps overlap | `skip_locked` partitions runs; double-dispatch absorbed by idempotency + constraint |
| Redelivery and stall re-dispatch race | Skip check, then unique constraint; duplicate rows impossible |
| Message hard-deleted mid-run | M2M cascade shrinks the plan; run converges |
| Run never progresses (bad config, dead provider) | FAILED after 3 stalls with an error message; edits unblock |
| Beat down | Evals stall alongside scheduled messages and event timeouts — existing, monitored dependency; no new risk class |

## Why this over the alternatives

**Small batches + chord.** A chord needs every batch's completion recorded in Redis; one lost member strands the run with no recovery. Here completion is recomputed from the DB every tick, and waves also fix queue starvation.

**Rolling window (each batch claims-and-enqueues the next).** Same bounds, but coordination is smeared across every batch's tail, and two failure modes (lost broker message, lost dispatcher) still needed a manual "kick" command. The sweep centralizes the logic in one function and self-heals everything, costing up to one tick of idle per wave boundary.

**Chord-chained waves.** Wave barriers via chained chords serialize stragglers and multiply the fragile chord completions. Rejected outright.

**Priority queues (Redis `priority_steps`).** Reorders the queue but bounds neither slot occupancy nor LLM concurrency; needs transport config, `prefetch_multiplier=1`, and careful rollout. Still a reasonable future layer for interactive-vs-bulk work; nothing here conflicts with it.

**Dedicated eval queue + worker service.** Hard isolation, at the cost of a new ECS service. The sweep achieves the same bounds in application code; if eval volume grows, add the queue later — the sweep carries over unchanged.

**Per-task `rate_limit`.** Enforced after dequeue; workers hoard rate-limited tasks in memory and interactive tasks still starve. Rejected.

## Out of scope

- `stop_timeout` / deploy grace period (ocs-deploy; already handled).
- Other non-idempotent tasks in this module (CSV import etc.).
- Priority queues / dedicated eval queue (compatible future layers).

## Tests (TDD order)

1. Plan freezing: `config.run()` populates `scoped_messages` for FULL, PREVIEW, DELTA and snapshots `evaluator_ids`; later dataset additions don't change the plan.
2. Sweep dispatches wave 1 for a PENDING run: ≤ WINDOW batches of ≤ BATCH_SIZE, `in_flight`/`wave_dispatched_at` set before dispatch, status → PROCESSING.
3. Half-done wave + fresh results → sweep no-ops.
4. Completed wave → next wave dispatched from `remaining`; `in_flight` replaced.
5. Stall past STALL_TIMEOUT → only unfinished messages re-dispatched, `wave_dispatched_at` reset; 3 consecutive stalls → FAILED.
6. Completion: all pairs present → guarded transition fires aggregates + stale-tag reversal exactly once; concurrent second sweep no-ops.
7. Batch task options: `acks_late is True`, `soft_time_limit == 240`.
8. Idempotency: re-run only evaluates missing evaluators; bot generation skipped when nothing is missing.
9. Duplicate `(run, message, evaluator)` raises `IntegrityError`; task-level handling turns the race into a no-op.
10. Progress publishing: each tick writes (done, total) for active runs to the result backend under `run.job_id`; completion writes SUCCESS; values match DB counts after re-dispatch (task-id churn doesn't skew them).
11. Message deleted mid-run (bulk delete bypassing guards) → run still completes.
12. Existing chunk/chord dispatch tests updated to the new shape.
