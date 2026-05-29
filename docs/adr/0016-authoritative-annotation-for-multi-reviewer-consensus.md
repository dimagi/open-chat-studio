# ADR-0016: Authoritative annotation for multi-reviewer consensus

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-29</p>

<p class="adr-meta">Extends: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a></p>

## Context

[ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md) lets an `AnnotationItem` collect multiple submitted `Annotation` rows — one per reviewer, up to `num_reviews_required`. Downstream consumers (the score writer at [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md), concordance at [ADR-0014](0014-minimal-read-side-concordance-view.md), exports, the aggregate at [ADR-0017](0017-eager-aggregation-of-submitted-annotations.md)) need one unambiguous "answer for this item" when reviewers disagree, and the item-status machine must distinguish "quota met, no winner" from "winner picked". This has to cover single-reviewer queues with zero ceremony and multi-reviewer queues with explicit, auditable resolution, in one model.

## Decision

A boolean `is_authoritative` flag on `Annotation`, with `authoritative_set_by` (nullable FK to `AUTH_USER_MODEL`) and `authoritative_set_at`:

- **At most one authoritative per item, DB-enforced** by the `one_authoritative_annotation_per_item` partial `UniqueConstraint` on `(item)` where `is_authoritative=True`.
- **Single-reviewer queues auto-mark the first submission** (`num_reviews_required == 1`, none yet authoritative) in the submitting transaction; `authoritative_set_by` is left null to distinguish auto-marking from an admin override.
- **Multi-reviewer queues require an explicit set** behind `human_annotations.change_annotationqueue`. The endpoint clears the flag on siblings before setting it, so the DB constraint is a backstop, never the user-visible failure.
- **Concurrent submissions are serialised** by `select_for_update()` on the `AnnotationItem` before auto-marking and incrementing `review_count` — the second submission observes the first's authoritative and skips.
- **`AnnotationItem.status` is derived** on each save into `AnnotationItemStatus`: `PENDING` (no reviews), `IN_PROGRESS` (below quota), `AWAITING_RESOLUTION` (multi-reviewer at quota, no winner), `COMPLETED` (single-reviewer at quota, or any item with an authoritative), `FLAGGED`.
- **`FLAGGED` is sticky** — recompute returns early; only an explicit unflag resets it to `PENDING` and re-derives.

## Consequences

- **Positive:** One DB-enforced "the answer" signal across writers, readers, and exports. The score writer ([ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)) records Scores for *every* submitted annotation (preserving inter-rater data); concordance ([ADR-0014](0014-minimal-read-side-concordance-view.md)) and the aggregate ([ADR-0017](0017-eager-aggregation-of-submitted-annotations.md)) filter to authoritative-or-fallback at read time.
- **Positive:** Auto-marking single-reviewer queues keeps the common case zero-ceremony — most queues never enter `AWAITING_RESOLUTION`.
- **Positive:** The row lock makes the constraint untriggerable under normal load; it's the safety net, not the contention point.
- **Positive:** Sticky `FLAGGED` keeps a broken item surfaced for admin attention regardless of later reviews; unflagging is explicit and auditable.
- **Negative:** `is_authoritative` conflates "auto-set" and "admin-picked"; only `authoritative_set_by IS NULL` distinguishes them, so override reporting must read the `SetAuthoritative` audit log, not infer from the field.
- **Negative:** Unresolved multi-reviewer items accumulate in `AWAITING_RESOLUTION` indefinitely — no auto-resolution by majority. Deliberate, but it needs admin discipline.
- **Negative:** Editing a submitted annotation keeps its authoritative flag, so a reviewer revising after an admin pick is invisible to the aggregate until the admin re-picks.

## Alternatives considered

- **Implicit majority vote:** rejected — silent and easy to mis-tally for categorical rubrics; admins choosing multi-reviewer usually want adjudication. A "suggest majority" affordance can layer on later without a schema change.
- **A separate consensus row:** rejected — doubles the write path and splits the score-writer dispatch ([ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)) across two models; the flag-on-the-winning-row keeps one source of truth with DB-enforced uniqueness.
- **A `consensus_annotation` FK on `AnnotationItem`:** rejected — harder to enforce "exactly one chosen child" than the partial unique constraint, and makes "no consensus yet" a nullable tri-state for readers.
- **Resolve in a Celery task:** rejected — single-reviewer auto-mark must be visible to the submitting request, which reads `is_authoritative` immediately for the score write and status recompute.
