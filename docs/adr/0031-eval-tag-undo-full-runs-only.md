# ADR-0031: Eval-driven tag undo operates on FULL runs only

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-02</p>

<p class="adr-meta">Extends: <a href="0020-delta-evaluation-runs-scoped-to-appended-messages.md">ADR-0020</a></p>

## Context

Evaluation runs apply tags via evaluator-tag rules, and users need to undo a run's tagging when an evaluator misbehaves. Runs come in three types ([ADR-0020](0020-delta-evaluation-runs-scoped-to-appended-messages.md)): FULL re-tags the whole dataset, DELTA tags only newly appended messages, PREVIEW never applies tags.

DELTA runs are created in exactly one place (auto-population), always over brand-new sessions deduped by `session_id`. So DELTA message-sets are pairwise disjoint and disjoint from prior FULL runs, while a FULL run covers everything. The runs partition the message space, and the live tag set is always `{latest FULL run} ∪ {DELTAs after it}`.

## Decision

We will allow tag undo only on the latest completed FULL run of a config, undoable once; DELTA and PREVIEW runs are never directly undoable.

- Undoing a FULL run restores the previous epoch as a run set — the prior FULL plus the DELTAs between it and the undone run. The partition makes that set the complete prior state, so no per-message walk-back is needed.
- Live/superseded state is one `EvaluationRun.tags_archived` boolean, not a per-`AppliedTag` flag. A FULL completion archives every other active run; a DELTA completion archives nothing; undo archives the undone run and reactivates its restore set. The active set is the runs with `tags_archived=False`, which drives both attribution and undo eligibility.
- `EvaluationConfig.run` rejects a DELTA whose `scoped_messages` overlap any prior run, failing loudly rather than silently corrupting undo.

## Consequences

- Undo is a single-step revert of the latest epoch; "fix the evaluator and re-run" replaces undoing arbitrary history.
- Cheap: supersede is one `UPDATE`, undo touches a bounded run set, no per-message bookkeeping.
- `tags_archived` records current state, not a history of past undos.
- Correctness is coupled to the DELTA invariant; any new DELTA-creation path must keep scoped messages disjoint or the guard rejects it.
- DELTA tags revert only indirectly, by undoing the FULL run that re-covered their sessions.

## Alternatives considered

- **Per-row `AppliedTag.archived` + per-message walk-back** — rejected: more state and queries for a result the run set gives directly under the invariant.
- **Undo any historical run** — rejected: each FULL re-tags everything, so only the latest revert is coherent.
- **Make DELTA runs undoable** — rejected: a DELTA supersedes nothing, so there is no prior state to restore.
