# ADR-0013: Dual-write Scores from evaluations and annotations

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Extends: <a href="0012-score-value-layer-in-apps-assessments.md">ADR-0012</a></p>

<p class="adr-meta">Related: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a>, <a href="0016-authoritative-annotation-for-multi-reviewer-consensus.md">ADR-0016</a>, <a href="0017-eager-aggregation-of-submitted-annotations.md">ADR-0017</a></p>

## Context

[ADR-0012](0012-score-value-layer-in-apps-assessments.md) introduced `Score` as the shared value layer, but it only earns its keep if both producers populate it reliably on live writes. The two paths have different lifecycles: `EvaluationResult` rows are created in a Celery worker, while `Annotation` rows ([ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md)) are created inside Django request/response cycles via `Annotation.save`.

Writers must be idempotent (re-running an evaluator or re-submitting an annotation leaves a clean set of Scores) and must not roll back a successful parent write when a Score write fails. Separately, `EvaluationResult` and `Annotation` rows that pre-date `Score` must be backfilled before the read-side view ([ADR-0014](0014-minimal-read-side-concordance-view.md)) is useful.

## Decision

We will populate `Score` via two single-responsibility writers in `apps/assessments/score_writers.py`, invoked at the right point in each subsystem's lifecycle, plus an `IdempotentCommand` for backfill:

- **Automated path.** The writer is called from the Celery evaluator task after each `EvaluationResult` is created, wrapped in a `try/except` that logs and swallows. It is *not* invoked from `EvaluationResult.save`, keeping persistence free of cross-app side effects. Error payloads, missing sessions, and non-dict payloads are skipped.
- **Human path.** The annotation writer is called from `Annotation.save` on every submitted save (initial submission and edits while still `SUBMITTED`), after the wrapping `transaction.atomic()` block, wrapped in a `try/except` that logs and swallows.
- **Idempotency is delete-then-bulk-create per artefact.** Each writer deletes the existing `Score` rows scoped to the artefact (filter on `automated_result` or `review`) then bulk-creates fresh ones inside `transaction.atomic()`. With the partial unique constraints from [ADR-0012](0012-score-value-layer-in-apps-assessments.md), re-runs, re-submissions, and backfill top-ups are safe overwrites.
- **`Score.target` is `item.session` only.** Annotations on message-only items are skipped; `ChatMessage` is excluded as a Score target in the unified design.
- **Scores are written for every submitted annotation, regardless of `is_authoritative` ([ADR-0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md)).** Non-authoritative annotations are preserved for future inter-rater-reliability work; the authoritative filter happens at read time (see [ADR-0014](0014-minimal-read-side-concordance-view.md)).
- **Type dispatch.** Python `bool` → `BOOLEAN` stored as 0/1 in `value_numeric`; numeric scalars → `NUMERIC` in `value_numeric`; strings → `CATEGORICAL` in `value_string`. A schema declaration of `type: choice` forces `CATEGORICAL` regardless of Python type, so values like `"0"`/`"1"` aren't misclassified. `None` and non-scalar containers are skipped with a warning.
- **Historical backfill is a `backfill_initial_scores` `IdempotentCommand`.** It iterates existing `EvaluationResult` and `Annotation` rows, pre-filtering to those with a session target, and commits per-row so failures stay local. Operators run it manually after the schema migration deploys; a follow-up `RunDataMigration(..., force=True)` migration tops up rows created between the manual run and that deploy.

## Consequences

- **Positive:** Writer-level idempotency plus DB-level partial unique constraints converge on the same clean state for re-runs, re-submissions, and backfill top-ups.
- **Positive:** The same two writers serve both live dual-write and backfill — one code path, one test surface.
- **Positive:** Hooking every submitted save (not just `is_new`) keeps Scores in lockstep with reviewer edits.
- **Positive:** `try/except` outside the parent transaction means an isolated Score write failure does not corrupt the evaluator run or fail the annotation submission; concordance accepts eventual consistency for resilience.
- **Negative:** A swallowed failure leaves a silent inconsistency — the parent row exists but its Scores don't. Operators must monitor the failure log and re-run the backfill to repair; there is no automatic retry.
- **Negative:** Every submitted edit (even no-op saves) issues a `DELETE` + `bulk_create` against `Score`. Negligible at current scale; if edits become hot we'd short-circuit when `data` is unchanged.
- **Negative:** The cross-app import from `apps.human_annotations.models` to `apps.assessments.score_writers` is module-level. No circular import materialised (`apps.assessments` references `human_annotations.Annotation` only via a string-form FK), but re-introducing a cycle would force a local import inside `save`.
- **Negative:** Manual `manage.py` backfill adds a deploy-time step; the two-phase pattern accepts this to avoid blocking deploys on long backfills.

## Alternatives considered

- **Write Scores inside `EvaluationResult.save` / `Annotation.save`:** rejected for the eval side → couples persistence to a side effect any caller (admin shell, tests) could trigger. Accepted for the annotation side because `Annotation.save` already does post-save bookkeeping (item review counts, queue aggregate recomputes per [ADR-0017](0017-eager-aggregation-of-submitted-annotations.md)).
- **Hook on `is_new and SUBMITTED` only:** rejected → reviewers revise in-place while still `SUBMITTED`, so concordance would serve stale judgments until the next backfill.
- **Use Django signals (`post_save`):** rejected → signals hide the side effect from the call site and bypass the explicit `try/except` boundary.
- **Run the writer inside the parent `transaction.atomic()`:** rejected → a Score writer failure would roll back the `EvaluationResult` / `Annotation` write, losing reviewer work.
- **Auto-run backfill as a data migration in the schema-migration PR:** rejected → a synchronous data migration could time out the deploy; the two-phase manual-run-then-`RunDataMigration(force=True)` top-up is the project standard for this size.
