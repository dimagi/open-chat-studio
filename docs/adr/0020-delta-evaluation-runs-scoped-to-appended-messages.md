# ADR-0020: Delta evaluation runs scoped to newly appended messages

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-31</p>

<p class="adr-meta">Extends: <a href="0019-poll-source-experiments-to-auto-populate-eval-datasets.md">ADR-0019</a></p>

## Context

With auto-population ([ADR-0019](0019-poll-source-experiments-to-auto-populate-eval-datasets.md)) appending new rows to datasets continuously, re-running full evaluations on every append would re-evaluate every previously-scored row. Teams want to evaluate only the rows produced by a tick, while keeping manual evaluation workflows unchanged.

## Decision

We will add a delta evaluation run type that scopes work to a specific subset of dataset messages, and an opt-in flag on `EvaluationConfig` that triggers a delta run automatically when the auto-population path appends rows.

- `EvaluationRunType.DELTA` joins `FULL` and `PREVIEW` as a run-type choice.
- `EvaluationRun.scoped_messages` is a M2M to `EvaluationMessage`, populated at enqueue for `DELTA` runs and empty for `FULL` / `PREVIEW`. The scope is frozen at enqueue, so concurrent appends mid-flight don't change what the in-flight run evaluates.
- `EvaluationConfig.auto_run_on_append` (default `False`) opts a config in. When auto-population appends rows, every opted-in config on the dataset gets a `DELTA` run scoped to those rows. The trigger is invoked from a `transaction.on_commit` hook so a rolled-back append never fires evaluations.
- The auto-trigger fires only from the polling path; manual filter-import and CSV-import paths are intentionally untouched.
- `EvaluationConfig.run` accepts an optional `scoped_messages` argument that pins the M2M before dispatching `run_evaluation_task`.
- `run_evaluation_task` branches on type: `PREVIEW` samples, `DELTA` reads `scoped_messages`, `FULL` reads the full dataset.
- The results view filters by `scoped_messages` for `DELTA` runs so the UI shows only the rows that were evaluated.
- Evaluator-tag rules apply to `DELTA` runs the same as `FULL` (the tag-rule gate only skips `PREVIEW`).

## Consequences

- Per-tick cost is bounded by the append size, not the full dataset.
- Opting in cannot inadvertently re-trigger from a manual import, because the auto-trigger is wired only into the polling path.
- Stable scope semantics: the M2M is captured at enqueue, so a run reports results for exactly the rows it was asked to evaluate even if the dataset grows mid-flight.
- Adding new append entry points later (event-driven ingestion, manual import auto-trigger) requires explicit wiring — they don't inherit the auto-trigger.
- Datasets with many opted-in configs and frequent ticks will see N delta runs per tick; cost discipline is the operator's responsibility.

## Alternatives considered

- **Auto-trigger on every append path (manual filter import, CSV import)** — rejected for v1: would change the behaviour of existing manual workflows. Per-path opt-in can be added later.
- **Re-run the full evaluation on every append** — rejected: re-evaluates already-scored rows and multiplies cost linearly with append frequency.
- **Store the scope as a filter expression instead of a M2M** — rejected: would re-resolve on access and pick up rows added after enqueue, breaking the stable-scope property.
- **Separate `EvaluationRun` subclass for delta runs** — rejected: a type discriminator plus nullable M2M is simpler and fits the existing run model.
