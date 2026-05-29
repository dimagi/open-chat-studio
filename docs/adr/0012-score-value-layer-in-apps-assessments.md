# ADR-0012: Lean Score value layer in apps/assessments

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Related: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a></p>

## Context

Two independent subsystems produce per-session judgments as opaque JSON: automated evaluation (`apps.evaluations`, via `EvaluationResult.output`) and human review (`apps.human_annotations` per [ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md), via `Annotation.data`). The dogfood pilot for "basic concordance" needs to compare one shared categorical field (an LLM judge's answer versus the human authoritative answer) without an ad-hoc JSON-versus-JSON join.

We do not want to commit to the full unified-assessment design before its larger pieces are ratified. So we introduce only its value-storage layer, using its terminal column names, with enough flexibility that future targets and source types are additive rather than schema-breaking.

## Decision

We will add a new Django app `apps.assessments` with a single model, `Score`, as the shared typed-value layer:

- **One row per (target, field, source).** A `Score` carries a `name`, a `data_type` enum (`NUMERIC` | `CATEGORICAL` | `BOOLEAN`), and split-column storage (`value_numeric` `DecimalField(20,6)`, `value_string` `TextField`). A `CheckConstraint` named `score_value_present` requires at least one value column populated.
- **Target is a `GenericForeignKey` from day one.** `target_content_type` + `target_object_id` form the polymorphic target. Only `ExperimentSession` is exercised in v1; adding `Trace` or `EvaluationMessage` later is non-breaking.
- **Source provenance via typed FKs plus a `source` enum.** `automated_result` (FK to `evaluations.EvaluationResult`) and `review` (FK to `human_annotations.Annotation`) are mutually-exclusive nullable FKs recording the producing artefact. `source` is a `TextChoices` enum: `LLM_JUDGE`, `PROGRAMMATIC`, `HUMAN_REVIEW`, plus reserved `USER_FEEDBACK` and `SYSTEM` with no producer in v1.
- **Idempotency enforced by partial unique constraints.** `score_unique_per_automated_result_field` covers `(automated_result, name)` where `automated_result IS NOT NULL`; `score_unique_per_review_field` covers `(review, name)` where `review IS NOT NULL`. This lets writers safely delete-and-recreate.
- **`team` denormalised via `BaseTeamModel`.** Set at write time from `EvaluationResult.team` / `Annotation.team` so queries scope by team without an extra join.
- **Booleans land in `value_numeric` as 0/1.** The `data_type=BOOLEAN` marker preserves rendering intent while letting aggregation treat booleans as numeric.
- **Field names align with the unified design's terminal vocabulary.** `automated_result` and `review` (not `evaluation_result` / `annotation`) are chosen now so eventual model renames change only the FK targets.
- **Defer everything else.** No `Assessment`, `AssessmentSchema`, `AssessmentRun`, `RoutingRule`, `participant` FK, `score_config`, or `comment` — each becomes a nullable addition when its use case arrives.

## Consequences

- **Positive:** Both subsystems dual-write into one queryable surface (see [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)); future consumers read one model (concordance per [ADR-0014](0014-minimal-read-side-concordance-view.md), inter-rater reliability, cross-source aggregation).
- **Positive:** `GenericForeignKey` from day one makes adding `Trace` or `EvaluationMessage` targets purely additive.
- **Positive:** Terminal column names make the eventual `EvaluationResult` / `Annotation` renames a model rename, not a `Score` schema migration.
- **Positive:** Partial unique constraints make re-runs and re-submissions safe at the database layer.
- **Negative:** A `GenericForeignKey` is harder for the ORM to optimise than a dedicated FK; the composite index `(target_content_type, target_object_id, name, source)` mitigates the v1 query pattern.
- **Negative:** Split columns plus a `data_type` discriminator cannot represent anything richer than scalar numeric/categorical/boolean (e.g. structured rubrics) — that waits for the deferred pieces.
- **Negative:** Reserved enum values (`USER_FEEDBACK`, `SYSTEM`) have no producer in v1; tooling must treat them as forward-compat placeholders.

## Alternatives considered

- **Ad-hoc JSON joins between `EvaluationResult.output` and `Annotation.data`** → rejected; every consumer would re-invent field discovery, type coercion, and idempotency.
- **Ship the full unified-assessment model now (`Assessment`, `AssessmentSchema`, `AssessmentRun`, routing tables)** → rejected; the pilot needs none of it and it commits us to unratified routing and schema-catalogue decisions.
- **Typed FK to `ExperimentSession` instead of `GenericForeignKey`** → rejected; would force an additive migration once `Trace` / `EvaluationMessage` targets come online.
- **Single JSON `value` column instead of split `value_numeric` / `value_string`** → rejected; gives up cheap numeric aggregation and forces readers to disambiguate types in Python.
- **Validate one-of (`automated_result`, `review`) via a `CheckConstraint`** → skipped; the partial unique constraints plus the `source` enum already prevent a malformed idempotent write.
