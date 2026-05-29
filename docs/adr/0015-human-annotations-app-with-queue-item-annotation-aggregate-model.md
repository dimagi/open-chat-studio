# ADR-0015: Dedicated human_annotations app with queue/item/annotation/aggregate model

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-29</p>

## Context

Structured human review of chat sessions — labelling conversations against a shared rubric, gathering multi-reviewer judgments, exporting them, comparing them to automated judgments — is a recurring need. `Tag` / `CustomTaggedItem` gave free-form annotation but no schema, reviewer assignment, review-count tracking, or aggregation.

The larger fork was whether to build this on top of `apps.evaluations`. That subsystem predates the annotation work and was designed entirely around automated evaluation: Celery-driven, idempotent re-runs over a fixed dataset. When this work began there was no settled picture of how the two would interact, and the immediate need was progress. Its data models and workflows did not fit interactive, per-reviewer, editable human review, and retrofitting them would have meant re-architecting a substantial part of evaluations — so we built a separate peer app. (See Consequences for the hindsight: the crossover workflows that emerged later have made this worth revisiting.)

## Decision

A dedicated `apps.human_annotations` app, organised around four `BaseTeamModel` records:

- **`AnnotationQueue`** — rubric + reviewer container: a `schema` (`SanitizedJSONField` mapping field names to `FieldDefinition` entries — the int/float/choice/string union `apps.evaluations` uses for evaluator output); `num_reviews_required` (1-10, `num_reviews_required_range` `CheckConstraint`); a `QueueStatus` enum (`ACTIVE`/`PAUSED`/`COMPLETED`/`ARCHIVED`); and `assignees` M2M to `AUTH_USER_MODEL`. `(team, name)` unique.
- **`AnnotationItem`** — the unit of work: an `AnnotationItemType` enum (`SESSION`/`MESSAGE`) over mutually-exclusive FKs to `experiments.ExperimentSession` and `chat.ChatMessage`, with partial unique constraints `unique_session_per_queue` / `unique_message_per_queue`. Carries a denormalised `review_count` and an append-only `flags` field.
- **`Annotation`** — one reviewer's submission; `(item, reviewer)` unique. `data` (`SanitizedJSONField`) keys must match the queue `schema`. An `AnnotationStatus` enum (`DRAFT`/`SUBMITTED`) gates whether the row counts toward review counts and aggregates.
- **`AnnotationQueueAggregate`** — `OneToOneField` to the queue caching per-field aggregates (see [ADR-0017](0017-eager-aggregation-of-submitted-annotations.md)).

Plus:

- **Reuse `FieldDefinition` from `apps.evaluations`, not a fork.** Queue rubrics and evaluator output schemas are the same shape, so one validator covers both — and [ADR-0012](0012-score-value-layer-in-apps-assessments.md)'s `Score` writes type-dispatch uniformly across automated and human paths.
- **Lock `schema` and `num_reviews_required` once any item has a review** (`items.filter(review_count__gt=0).exists()`); only per-field `required` stays mutable. Changing fields mid-queue would silently invalidate prior submissions.
- **Role-based visibility via `AnnotationQueue.objects.visible_to(user, team)`:** all team queues for holders of `human_annotations.add_annotationqueue`, else only queues where the user is an assignee. An empty `assignees` set means team-wide, non-empty means restricted — invite-only vs team-wide without a separate flag.
- **Gate entry points behind the `flag_human_annotations` Waffle flag.** It controls discovery (nav, "Add to queue" affordances), not data access; the management views are not flag-gated.

## Consequences

- **Positive:** Stable FK targets for downstream consumers ([ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md) score writer, [ADR-0014](0014-minimal-read-side-concordance-view.md) concordance, CSV/JSONL exports). `Annotation` is referenced by `assessments.Score.review` and survives the eventual `Annotation → Review` rename.
- **Positive:** Sharing `FieldDefinition` means one validation, widget mapping, and aggregation dispatch ([ADR-0017](0017-eager-aggregation-of-submitted-annotations.md)) across both subsystems; new field types ship in one place.
- **Positive:** Schema-lock with mutable per-field `required` lets admins re-tighten which fields are mandatory after launch without redefining what was measured.
- **Negative:** A fifth `BaseTeamModel` is one more queryset to team-scope; every view must filter through `team=request.team` or `visible_to(...)`.
- **Negative:** Locking on first review (not first submission, not `ACTIVE`) means a queue stays editable through reviewer assignment and locks the moment work begins — easy to miss; the form disables fields on render to signal it.
- **Negative:** `flags` is append-only and queryable only via JSON operators; `AnnotationItemStatus.FLAGGED` is the indexed signal for listings.
- **Negative (hindsight):** Building a peer app rather than extending `apps.evaluations` has been worth revisiting. The crossover workflows — judge-vs-human concordance, human labels as ground truth — mean the two are far less independent than assumed. The shared `Score` layer ([ADR-0012](0012-score-value-layer-in-apps-assessments.md)), dual-write ([ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)), and concordance view ([ADR-0014](0014-minimal-read-side-concordance-view.md)) are the reconciliation surface the split deferred rather than avoided. The right call for making progress without a clear vision, but the cost has been ongoing bridging rather than one upfront design.

## Alternatives considered

- **Extend `Tag` / `CustomTaggedItem` with a "queue":** rejected — flat strings, no typed schema, no per-reviewer uniqueness, nowhere to hang `review_count` or an aggregate.
- **Fold into `apps.evaluations` as a "manual evaluator":** rejected — the main fork, covered in Context. Different lifecycle (worker-driven re-runs vs interactive editable reviews), poor data-model fit, and constant reviewer-vs-evaluator disambiguation in shared code.
- **`GenericForeignKey` for `AnnotationItem`:** rejected — only two target types; dedicated FKs make the partial unique constraints and `select_related("session__experiment", "message")` clearer.
- **One global `Annotation` shared across both paths:** rejected — automated results come from `EvaluationResult.output` with a worker-driven lifecycle and would force a discriminator on every column. [ADR-0012](0012-score-value-layer-in-apps-assessments.md) is the shared layer where the paths converge.
- **Free-form schema edits any time:** rejected — changing "yes/no" to a 1-5 scale after submissions invalidates them with no migration path. Per-field `required` is the one safe mid-queue change.
