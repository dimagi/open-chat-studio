# ADR-0017: Eager per-submission aggregation into a per-queue record

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-29</p>

<p class="adr-meta">Extends: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a></p>

## Context

The queue detail page shows per-field aggregates — mean/std-dev for numeric fields, value-count histograms for choice fields. The data is already shaped (`Annotation.data` keyed by the [ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md) schema fields) and `apps.evaluations` already has an `aggregate_field` helper that dispatches on value type. Two questions: *when* to compute (per submission / per render / scheduled) and *where* to store it. Dogfood queues run to hundreds of items with a handful of fields each — cheap enough to recompute eagerly, large enough that the detail page shouldn't scan all submissions per load.

## Decision

Recompute eagerly on every submitted-annotation save, persisted in a denormalised `AnnotationQueueAggregate`:

- **One row per queue** — `OneToOneField` to `AnnotationQueue` with a single `aggregates` `JSONField`, read via `select_related("aggregate")`. No aggregation in the request path.
- **Compute once per submission, outside the parent transaction.** After the locked block that updates `review_count` and item status ([ADR-0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md)), the aggregator runs wrapped in a logging `try/except` — an aggregation failure must never roll back a reviewer's submission. Same resilience stance as the score writer ([ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)).
- **Per item, prefer the authoritative annotation, else all submitted.** Once resolved, authoritative ([ADR-0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md)) is the single contributor; pre-resolution every submission counts, so the admin sees the spread.
- **Text fields are excluded** — free-text has no meaningful aggregate and would flood the result with unique values.
- **Edits recompute too** (`update_or_create` keyed on `queue`), so a revised answer never leaves stale numbers.
- **`None` and missing keys are skipped, not zero-filled;** explicit numeric zeros are kept.

## Consequences

- **Positive:** The detail page reads one cached `JSONField` — no hot-path aggregation, no N+1, no Celery roundtrip; renders are independent of queue size.
- **Positive:** Recomputing on every submitted save (not just new rows) keeps the aggregate consistent with reviewer edits — same reason as [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md).
- **Positive:** Swallowing aggregation errors outside the parent transaction means a future bad value type can't corrupt the submission or the UI; the aggregate retries on the next save.
- **Negative:** Recompute is O(items × annotations) per save — sub-millisecond at dogfood scale, but visible per-submission latency for a ~10,000-item queue. Incremental aggregation would need a structural change (the single `JSONField` holds no per-item state).
- **Negative:** A swallowed failure is visible only in the log — admins see a stale aggregate with no UI warning and must rebuild manually.
- **Negative:** Free-text fields don't appear on the aggregate panel; reviewer comments at scale need the CSV/JSONL export.
- **Negative:** Any path that creates a submitted annotation outside the normal save flow (data migrations, shell) bypasses the recompute; the aggregator is callable directly but must be remembered.

## Alternatives considered

- **Compute on read:** rejected — a per-render scan of every submission, degrading non-linearly with queue size on the primary admin surface.
- **Celery task via signal:** rejected — operational overhead (queue, worker, retry) and harder-to-trace indirection for a sub-millisecond computation; the call site is one function if write latency ever forces a move.
- **Store aggregates on `AnnotationQueue`:** rejected — the queue is fetched on the list page and would carry the payload everywhere; a separate `OneToOneField` keeps the row lean.
- **Top-N for text fields:** rejected for v1 — free-text rarely clusters into countable values; export covers inspection.
- **Aggregate inside the parent transaction:** rejected — a failure would roll back the reviewer's submission, trading a display issue for lost work (same trade-off as [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md)).
