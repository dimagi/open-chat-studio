# ADR-0012: Lean Score value layer in apps/assessments

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Related: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a></p>

## Context

Open Chat Studio has two independent subsystems that produce per-session judgments: automated evaluation (`apps.evaluations`, writing `EvaluationResult.output` as opaque JSON) and human review (`apps.human_annotations` per [ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md), writing `Annotation.data` as opaque JSON). The dogfood pilot for "basic concordance" needed a way to compare one shared categorical field — an LLM judge's answer versus the human authoritative answer — without an ad-hoc JSON-versus-JSON join, and without committing to the full unified assessment design (`docs/design/unified-assessment.md`) before its larger pieces (`Assessment`, `AssessmentSchema`, `AssessmentRun`, routing rules) have been ratified. The constraint shaping this ADR was therefore: introduce only the value-storage layer that the unified design defines, in its terminal column names, with enough flexibility that future targets and source types are additive rather than schema-breaking.

## Decision

We will introduce a new Django app `apps.assessments` containing a single model, `Score`, as the shared typed-value layer:

- **Scope is one row per (target, field, source).** A `Score` carries a `name`, a `data_type` enum (`NUMERIC` | `CATEGORICAL` | `BOOLEAN`) and split-column storage (`value_numeric` `DecimalField(20,6)` and `value_string` `TextField`). A `CheckConstraint` named `score_value_present` requires at least one of the two value columns to be populated.
- **Target is a `GenericForeignKey` from day one.** `target_content_type` + `target_object_id` form the polymorphic target, even though only `ExperimentSession` is exercised in v1. Adding `Trace` or `EvaluationMessage` later is non-breaking.
- **Source provenance is preserved by typed FKs plus a `source` enum.** `automated_result` (FK to `evaluations.EvaluationResult`) and `review` (FK to `human_annotations.Annotation`) are mutually-exclusive nullable FKs that record which artefact produced this Score; `source` is a `TextChoices` enum (`LLM_JUDGE`, `PROGRAMMATIC`, `HUMAN_REVIEW`, plus reserved `USER_FEEDBACK` and `SYSTEM` values with no producer in v1).
- **Idempotency is enforced by partial unique constraints, not application logic.** `score_unique_per_automated_result_field` covers `(automated_result, name)` where `automated_result IS NOT NULL`; `score_unique_per_review_field` covers `(review, name)` where `review IS NOT NULL`. This matches the unified design's "artefact-level idempotency" and lets the writers safely delete-and-recreate.
- **`team` is denormalised via `BaseTeamModel`.** Set at write time from the parent `EvaluationResult.team` / `Annotation.team` so queries scope by team without an extra join.
- **Booleans land in `value_numeric` as 0/1, not in `value_string`.** The `data_type=BOOLEAN` marker preserves the original intent for rendering while letting future aggregation treat booleans as numeric without a special case.
- **Field names align with the unified design's terminal vocabulary.** `automated_result` and `review` (not `evaluation_result` / `annotation`) are chosen now so that when `EvaluationResult → AutomatedResult` and `Annotation → Review` renames happen, only the FK targets change.
- **Deferred everything.** No `Assessment` umbrella, `AssessmentSchema`, `AssessmentRun`, `RoutingRule`, `participant` FK, `score_config`, or `comment` field. Each is a nullable addition when its motivating use case arrives.

## Consequences

- **Positive:** Both subsystems can dual-write into a single queryable surface (see [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)). Future consumers (concordance view per [ADR-0014](0014-minimal-read-side-concordance-view.md), inter-rater reliability, cross-source aggregation) read one model.
- **Positive:** GenericForeignKey from day one means adding `Trace` or `EvaluationMessage` as targets is purely additive — no migration of existing rows.
- **Positive:** Terminal column names mean the eventual `EvaluationResult` / `Annotation` model renames are a model rename, not a Score schema migration.
- **Positive:** Partial unique constraints make re-runs and re-submissions safe at the database layer, not by writer convention alone.
- **Negative:** A `GenericForeignKey` is harder for the ORM to optimise than a dedicated FK; cross-target joins require `target_content_type` filters and lose the typed-FK ergonomics. The composite index `(target_content_type, target_object_id, name, source)` mitigates the v1 query pattern.
- **Negative:** Two value columns plus a `data_type` discriminator is more disciplined than a single JSON column but more constrained than the full unified design's `score_config` / per-field metadata. Anything richer than scalar numeric/categorical/boolean (e.g. structured rubrics) must wait for the deferred pieces.
- **Negative:** Reserved enum values (`USER_FEEDBACK`, `SYSTEM`) have no producer in v1; tooling and dashboards must treat them as forward-compat placeholders rather than active sources.

## Alternatives considered

- **Continue with ad-hoc JSON joins between `EvaluationResult.output` and `Annotation.data`:** rejected — every consumer would re-invent field discovery, type coercion, and idempotency rules; concordance becomes a one-off rather than a building block.
- **Ship the full unified-assessment data model now (`Assessment`, `AssessmentSchema`, `AssessmentRun`, routing tables):** rejected — the dogfood pilot doesn't need any of it, and shipping it would commit us to decisions on routing semantics, schema catalogues, and applied-filter modelling that haven't been ratified.
- **Typed FK to `ExperimentSession` instead of `GenericForeignKey`:** rejected — would require an additive migration the moment the unified design's other targets (`Trace`, `EvaluationMessage`) come online, exactly the kind of schema break we are trying to avoid.
- **Single JSON `value` column instead of split `value_numeric` / `value_string`:** rejected — gives up cheap numeric aggregation and forces every reader to disambiguate types in Python.
- **Validate one-of (`automated_result`, `review`) via a `CheckConstraint`:** considered and skipped — partial unique constraints already make a malformed Score impossible to write idempotently, and the `source` enum is the canonical provenance signal. Adding a mutual-exclusion check would duplicate intent the writers already enforce.
