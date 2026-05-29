# ADR-0014: Minimal read-side concordance view backed by Score

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Extends: <a href="0012-score-value-layer-in-apps-assessments.md">ADR-0012</a></p>

<p class="adr-meta">Related: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a>, <a href="0016-authoritative-annotation-for-multi-reviewer-consensus.md">ADR-0016</a></p>

## Context

The dogfood pilot for "basic concordance" wanted to compare an LLM judge's per-session answer against a human reviewer's authoritative answer for one shared categorical field. With `Score` ([ADR-0012](0012-score-value-layer-in-apps-assessments.md)) populated by both subsystems ([ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)), the question becomes: what read surface ships first? The full unified-assessment design proposes persisted concordance configurations, multi-source consensus aggregation, kappa/MAE/confusion-matrix statistics, and trend charts — none of which the dogfood pilot needs. We wanted the smallest read-side view that proves the value layer works end-to-end without locking in decisions about persisted concordance configs or aggregation semantics.

## Decision

We will ship a single Django `TemplateView` (`ConcordanceView`) mounted at `/a/<team_slug>/evaluations/concordance/` with a sidebar sub-item under Evaluations. The view's shape is deliberately constrained:

- **Selection state lives entirely in query parameters.** `?eval=<id>&queue=<id>&field=<name>&show=<bucket>` — there is no persisted concordance configuration. Re-running a comparison is a fresh URL, bookmarkable but disposable.
- **Candidate fields are the *name intersection* of the two schemas, narrowed to `type: choice` on both sides.** The eval-side schema is the union of the configured evaluators' `output_schema` declarations; the human-side schema is the queue's `schema` ([ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md)). Numeric fields are silently filtered out for v1; the data still flows into `Score` but is not surfaced here. If exactly one candidate exists, the view auto-selects it (the dogfood path); otherwise the picker is rendered.
- **Two `Score` queries, joined in Python.** The judge query filters by `source IN (LLM_JUDGE, PROGRAMMATIC)` and joins through `automated_result.run.config = eval_config`; the human query filters by `source = HUMAN_REVIEW`, joins through `review.item.queue = queue`, and crucially also requires `review.is_authoritative = True` ([ADR-0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md)). The two result sets are reduced to latest-`Score`-per-`target_object_id` on each side, then set-intersected to yield matched / eval-only / human-only buckets.
- **Aggregation is "latest Score per target per side."** Each side keeps the most-recent Score per `target_object_id`, ordered by `(created_at, id)` so ties resolve deterministically. This is a deliberate v1 stand-in for the unified design's per-source consensus (mean / mode).
- **`is_authoritative` is filtered at read time, not denormalised onto `Score`.** Multi-reviewer queues let humans toggle authoritativeness post-submission (tracked via `Annotation.authoritative_set_by` / `authoritative_set_at`); single-reviewer queues auto-mark on submission. Denormalising onto `Score` would require sync hooks on every authoritative-toggle. A query-time join through `review.is_authoritative` is cheap enough for the dogfood pilot.
- **Filter eval-side joins through `run.config`, not `evaluator`.** `Evaluator ↔ EvaluationConfig` is many-to-many, so filtering by evaluator would pull in Scores produced by *other* configs sharing the same evaluator. Joining through `automated_result.run.config` instead keeps the scope strictly inside the selected config.
- **A `?show=` toggle partitions the table** (`matched` | `eval_only` | `human_only` | `all`, default `matched`). Agreement count and percentage are computed only over `matched` rows.
- **The view is gated by a new team-managed waffle flag `flag_assessments_concordance`**, which lists `flag_evaluations` and `flag_human_annotations` as required (you can't compare what you can't produce). The view raises `Http404` from `dispatch` if any of the three is inactive.

## Consequences

- **Positive:** A reviewer sees side-by-side judge-vs-human values for one chosen field and an agreement count, which is the entire dogfood ask. No new persisted models or admin screens were needed.
- **Positive:** All state in query params means deep-linking, sharing, and bookmarking work, and the view is trivial to refactor when persisted configurations land later.
- **Positive:** Query-time authoritative filter means the view stays correct as reviewers toggle authoritativeness in multi-reviewer queues, without any cache-busting or denormalisation maintenance.
- **Positive:** Filtering by `run.config` (not `evaluator`) means concordance for one config never silently includes Scores from another config that happens to share an evaluator.
- **Positive:** Waffle flag gating with a dispatch-level 404 keeps the URL invisible to teams that haven't been opted in.
- **Negative:** "Latest Score per target" is a deliberate simplification — for matched rows where a session has multiple eval runs over time, only the most recent answer is shown. The agreement count therefore measures "most-recent agreement" rather than any temporal aggregation.
- **Negative:** Numeric and free-text fields are silently filtered out — a user inspecting the picker won't see why a field they expected is missing. Acceptable for the binary-choice dogfood case; the unified-design successor will surface numeric concordance with proper metrics (MAE, correlation).
- **Negative:** Items in `AWAITING_RESOLUTION` (multi-reviewer queues with no authoritative pick yet) silently drop out of the comparison. This is the correct semantics for "compare the judge against the resolved human answer," but the empty state needs to make it clear when many items are filtered out.
- **Negative:** No persistence of the chosen `(eval, queue, field)` tuple means power users must re-discover their preferred comparison each visit. Acceptable for the pilot; persisted configs are explicitly deferred to the unified design.

## Alternatives considered

- **Persist a `ConcordanceConfig` model now:** rejected — the unified design defines this surface (`Assessment` + `AssessmentSchema`); shipping a v1 version would lock in choices the dogfood pilot doesn't need to make.
- **Denormalise `is_authoritative` onto `Score`:** rejected — would require sync hooks on every authoritative toggle, including `authoritative_set_by` reassignments in multi-reviewer queues, and create a denormalisation drift risk for marginal query benefit at dogfood scale.
- **Filter by `automated_result__evaluator__in=eval_config.evaluators.all()` (the original design proposal):** rejected during implementation — `Evaluator ↔ EvaluationConfig` is M2M, so an evaluator shared across configs would pull foreign Scores into the comparison. The implementation pivoted to `automated_result__run__config=eval_config`, which keeps scope strictly inside the selected config.
- **Compute the join in SQL with a single query (FULL OUTER JOIN or two-step CTE):** rejected — Django ORM support for full outer joins is awkward, and the Python set-intersection on `target_object_id` is readable and bounded by the per-team result count.
- **Render Cohen's kappa / MAE / confusion matrix in v1:** rejected — explicit non-goal of the dogfood pilot; the data flowing into `Score` is sufficient for these metrics to land in a follow-up without schema changes.
- **CSV / JSONL export of the rows:** rejected — adds surface area before we know what an export should contain. The session-row link to the existing session detail page is the v1 escape hatch.
