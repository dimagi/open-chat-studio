# ADR-0019: Poll source experiments to auto-populate evaluation datasets

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-31</p>

## Context

Evaluation datasets were one-shot — populated via manual session pick, filter import, or CSV upload — and went stale unless someone refreshed them. Teams wanted datasets that continuously absorbed new sessions from a source chatbot matching saved filter criteria. The two viable mechanisms were event-driven hooks (signals on session end and tag changes) or periodic polling. Hooks demand careful coupling to many call sites and still need a backstop for post-hoc tag changes that fire long after a session was created — `CustomTaggedItem` writes don't bump `session.updated_at`, so a cursor-based ingester would miss them.

The feature is gated by the existing `flag_evaluations` waffle flag; teams with the evaluations app get auto-population without a separate opt-in.

## Decision

We will run a periodic Celery task (`auto_populate_eval_datasets`, every 5 minutes) that walks each enabled `DatasetAutoPopulationRule`, scans its source experiment for recent sessions matching the rule's filter, and appends matches to the parent `EvaluationDataset`.

- `DatasetAutoPopulationRule` is a `BaseTeamModel` carrying the parent dataset, source experiment, filter query string, enabled flag, and per-rule run metadata: `last_run_at`, `last_run_status` (`success` / `error` / `no_op`), `last_error`, `consecutive_failure_count`.
- Rules are restricted to session-mode datasets in v1; message-mode datasets are out of scope.
- Each tick re-scans within a configurable lookback window (`EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS`, default 30). The scan floor is `MAX(rule.created_at, now() - lookback)` — `created_at` is the forward-only floor, the lookback caps per-tick work.
- No high-water-mark cursor is stored on the rule. Dedup is performed at scan time by excluding sessions whose `id` is already in `dataset.messages`. This guarantees sessions that gain a matching tag after creation are picked up on a later tick.
- Each rule is processed inside its own transaction with `select_for_update(skip_locked=True)`, so concurrent beat workers never double-process the same rule. Per-rule exceptions are caught and recorded in a fresh transaction outside the (potentially rolled-back) atomic block.
- After three consecutive failures the rule is auto-disabled and an `ocs_notifications` notification fires.

## Consequences

- Ingestion latency is bounded by the 5-minute beat cadence; near-real-time ingestion is out of scope.
- Re-scan cost grows linearly with the lookback window. The 30-day default trades freshness against scan cost; tag changes older than the window will not be picked up.
- The forward-only floor means rules never absorb sessions older than the rule itself — backfilling historical traffic still requires a manual import.
- Auto-disable + notification surfaces broken rules without paging an operator.
- Per-tick state on the rule makes operational status visible directly on the dataset detail page, no separate audit log needed.

## Alternatives considered

- **Event-driven ingestion** (signals on `ExperimentSession.end()` and `CustomTaggedItem` writes) — rejected for v1: requires hooks at many call sites and still needs a polling backstop for tag-change races. Deferred until polling cadence proves too slow.
- **High-water-mark cursor on the rule** — rejected: a `last_seen_session_at` cursor would skip sessions whose matching tag is added after the cursor advances. `NOT IN dataset` dedup is the only correct approach when filter criteria depend on mutable state.
- **Lifecycle hook on `ExperimentSession.end()`** — deferred: useful for near-real-time but redundant with polling for v1.
- **Sampling sessions** (ingest only N% of matches) — deferred; mentioned in the source issue but not needed for v1.
