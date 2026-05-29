# ADR-0014: Minimal read-side concordance view backed by Score

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Extends: <a href="0012-score-value-layer-in-apps-assessments.md">ADR-0012</a></p>

<p class="adr-meta">Related: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a>, <a href="0016-authoritative-annotation-for-multi-reviewer-consensus.md">ADR-0016</a></p>

## Context

The dogfood pilot for "basic concordance" compares an LLM judge's per-session answer against a human reviewer's authoritative answer for one shared categorical field. With `Score` ([ADR-0012](0012-score-value-layer-in-apps-assessments.md)) now populated by both subsystems ([ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)), the question is what read surface ships first.

The full unified-assessment design proposes persisted concordance configs, multi-source consensus aggregation, and kappa/MAE/confusion-matrix statistics — none of which the pilot needs. We want the smallest read-side view that proves the value layer works end-to-end without locking in those decisions.

## Decision

We will ship a single Django `TemplateView` (`ConcordanceView`) at `/a/<team_slug>/evaluations/concordance/`, under a sidebar sub-item in Evaluations.

- **Selection state lives entirely in query parameters** (`?eval=`, `?queue=`, `?field=`, `?show=`). There is no persisted config; a comparison is a bookmarkable but disposable URL.
- **Candidate fields are the name intersection of the two schemas, narrowed to `type: choice` on both sides.** The eval side is the union of the configured evaluators' output schemas; the human side is the queue's `schema` ([ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md)). Numeric and free-text fields are filtered out for v1. A single candidate auto-selects; otherwise the picker renders.
- **Two `Score` queries, joined in Python.** The judge query filters `source IN (LLM_JUDGE, PROGRAMMATIC)`; the human query filters `source = HUMAN_REVIEW` and `review.is_authoritative = True` ([ADR-0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md)). Each side is reduced to latest `Score` per `target_object_id`, then set-intersected into matched / eval-only / human-only buckets.
- **Aggregation is "latest Score per target per side,"** ordered by `(created_at, id)` for deterministic ties. This is a v1 stand-in for the unified design's per-source consensus (mean / mode).
- **`is_authoritative` is filtered at read time, not denormalised onto `Score`.** Multi-reviewer queues let humans toggle authoritativeness after submission; denormalising would require sync hooks on every toggle. A query-time join is cheap enough at pilot scale.
- **Eval-side joins go through `automated_result.run.config`, not `evaluator`.** `Evaluator ↔ EvaluationConfig` is many-to-many, so filtering by evaluator would pull in Scores from other configs sharing that evaluator. Joining through `run.config` keeps scope inside the selected config.
- **A `?show=` toggle partitions the table** (`matched` | `eval_only` | `human_only` | `all`, default `matched`). Agreement count and percentage are computed over `matched` rows only.
- **The view is gated by the team-managed waffle flag `flag_assessments_concordance`,** which requires `flag_evaluations` and `flag_human_annotations`. Dispatch raises `Http404` if any of the three is inactive.

## Consequences

- **Positive:** A reviewer sees side-by-side judge-vs-human values for one field plus an agreement count — the entire dogfood ask — with no new persisted models.
- **Positive:** All state in query params makes the view deep-linkable, shareable, and trivial to refactor when persisted configs land.
- **Positive:** The query-time authoritative filter stays correct as reviewers toggle authoritativeness, with no cache-busting or denormalisation upkeep.
- **Positive:** Joining through `run.config` means concordance for one config never includes Scores from another config sharing an evaluator.
- **Positive:** Waffle gating with a dispatch-level 404 keeps the URL invisible to teams not opted in.
- **Negative:** "Latest Score per target" shows only the most recent answer, so the agreement count measures most-recent agreement, not any temporal aggregation.
- **Negative:** Numeric and free-text fields are silently filtered from the picker, so a user won't see why an expected field is missing; the unified-design successor will surface numeric concordance with proper metrics.
- **Negative:** Items in `AWAITING_RESOLUTION` (no authoritative pick yet) drop out; this is correct for "compare against the resolved human answer," but the empty state must signal when many items are filtered.
- **Negative:** Not persisting the `(eval, queue, field)` tuple means power users re-discover their comparison each visit; persisted configs are deferred to the unified design.

## Alternatives considered

- **Persist a `ConcordanceConfig` model now** → rejected; the unified design defines this surface, so a v1 would lock in choices the pilot doesn't need.
- **Denormalise `is_authoritative` onto `Score`** → rejected; needs sync hooks on every authoritative toggle and risks drift for marginal query benefit.
- **Filter by `automated_result__evaluator__in=...`** → rejected; the M2M `Evaluator ↔ EvaluationConfig` relation pulls in foreign Scores. Use `automated_result__run__config=eval_config` instead.
- **Compute the join in SQL (FULL OUTER JOIN or CTE)** → rejected; Django ORM full-outer-join support is awkward, and the Python set-intersection on `target_object_id` is readable and bounded by per-team result count.
- **Render Cohen's kappa / MAE / confusion matrix in v1** → rejected; explicit non-goal, and the `Score` data suffices for these metrics to land later without schema changes.
- **CSV / JSONL export of rows** → rejected; adds surface area prematurely. The session-row link to session detail is the v1 escape hatch.
