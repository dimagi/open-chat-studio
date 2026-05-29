# ADR-0017: Eager per-submission aggregation into a per-queue record

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-29</p>

<p class="adr-meta">Extends: <a href="0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md">ADR-0015</a></p>

## Context

The queue detail page surfaces per-field aggregates (mean / std-dev for numeric fields, value-count histograms for choice fields) so a queue admin can see how reviewers are answering across the rubric. The data is already shaped — every submitted `Annotation.data` is a dict keyed by the schema field names from [ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md), and `apps.evaluations` already exposes an `aggregate_field` helper that dispatches on the value type. Two questions had to be answered: *when* to compute (on every submission vs. on every render vs. on a schedule), and *where* to store the result. Reviewer dogfooding had hundreds of items per queue with handfuls of fields each — small enough to recompute eagerly without hot-path cost, large enough that the queue detail page would not want to scan all submissions per page-load.

## Decision

We will recompute aggregates eagerly on every submitted annotation save and persist them in a denormalised `AnnotationQueueAggregate` record:

- **One aggregate row per queue.** `AnnotationQueueAggregate` is a `BaseTeamModel` with a `OneToOneField` to `AnnotationQueue` and a single `aggregates` `JSONField`. The reverse accessor is `queue.aggregate` so the queue detail view does one `select_related("aggregate")` and reads the cached dict directly — no aggregation work in the request path.
- **Compute runs once per submission, inside the submission's write path but outside the parent transaction.** After the `Annotation.save` finishes the locked `transaction.atomic()` block that updates `review_count` and the item status (see [ADR-0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md)), the aggregator is invoked for the queue. It is wrapped in `try/except` that logs the exception and swallows it — an aggregation failure must never roll back a successful reviewer submission or surface as an error in the annotate UI. This matches the resilience pattern at [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md) for the score writer.
- **Per-item: prefer the authoritative annotation, fall back to all submitted.** For each item in the queue, the aggregator selects the contributing rows as `[authoritative] if any(is_authoritative) else all submitted`. Authoritative ([ADR-0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md)) is treated as the single source of truth for an item once resolved; pre-resolution, every reviewer's submission contributes to the aggregate so the admin sees the spread that informed their adjudication.
- **Text (string) fields are excluded.** `_get_aggregatable_fields(queue)` filters the queue schema to non-`string` types. Free-text answers don't have a meaningful aggregate, and including them would either inflate the result with thousands of unique values or force a separate sentinel in the read path.
- **Edits trigger recompute, not just new submissions.** Editing a submitted annotation re-runs the aggregator for the queue, so a reviewer revising their answer never leaves the queue detail page showing stale numbers. The `update_or_create` upsert keyed on `queue` makes this safe to repeat without a separate stale-row cleanup.
- **`None` values and missing keys are skipped, not zero-filled.** A reviewer who submitted before a field was added (back when the schema lock allowed it — pre-first-review per [ADR-0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md)) simply doesn't contribute to that field's aggregate. The aggregator treats absence and `None` identically; explicit zeros from numeric fields are kept.

## Consequences

- **Positive:** The queue detail page reads one cached `JSONField` — no aggregation work on hot read paths, no N+1 across thousands of annotations, no Celery roundtrip. Renders are unaffected by queue size.
- **Positive:** Hooking on every submitted save (not just `is_new`) keeps the aggregate consistent with reviewer edits — the same pattern the score writer adopted at [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md) for the same reason: stale derived data after an edit is a worse UX than the extra recompute.
- **Positive:** Running outside the parent transaction with a swallowing `try/except` means an aggregation bug (e.g. an unexpected value type from a future schema migration) cannot corrupt the reviewer's submission or break their UI. The submission lands; the aggregate retries on the next save.
- **Positive:** Excluding text fields keeps the aggregate compact and the schema discriminator (which fields produce numbers, which produce histograms) lives entirely in `FieldDefinition` types — no parallel "aggregable" flag.
- **Negative:** Recomputing the entire queue on every submission is O(items × annotations) per save. For dogfood-scale queues (hundreds of items, single-digit reviewers each), this is sub-millisecond; for a 10,000-item queue this becomes visible per-submission latency. The escape hatch is incremental aggregation, but the schema (single `JSONField`, no per-item state) doesn't support it without a structural change.
- **Negative:** A silently-swallowed aggregator exception is observable only in the log — the queue admin sees a stale aggregate but no UI warning. Operators must watch the `"Failed to recompute aggregates for queue …"` log line and rebuild manually if it fires repeatedly.
- **Negative:** Free-text fields are invisible on the aggregate panel of the queue detail page. Surfacing reviewer comments at queue scale requires the CSV/JSONL export, not the in-app view.
- **Negative:** Coupling the aggregator to `Annotation.save` means any code path that creates a submitted annotation outside the normal save flow (data migrations, shell creates) bypasses the recompute. The aggregator function is callable directly from such paths but has to be remembered.

## Alternatives considered

- **Compute aggregates on read in the queue detail view:** rejected — would force a per-render scan of every submitted annotation in the queue. Acceptable at dogfood scale but degrades non-linearly with queue size, and the queue detail page is the primary "did the review go well?" surface for admins.
- **Run aggregation in a Celery task triggered by a signal:** rejected — adds operational complexity (a queue, a worker, a retry policy) for what is currently a sub-millisecond computation. The signal indirection also makes the side effect harder to trace from the submission call site than the explicit call. We can move to Celery later if write latency becomes a problem; the call site is a single function.
- **Store aggregates in the parent `AnnotationQueue` as another `JSONField`:** rejected — the queue is queried frequently from the queue list page and would carry the aggregate payload along on every fetch. Splitting into a separate `OneToOneField` keeps the queue row lean and lets the list page omit the join.
- **Include text fields with a "top N values" summary:** rejected for v1 — free-text annotations from reviewers rarely cluster into countable values; a top-N would be noise. CSV/JSONL export covers the inspection case.
- **Run aggregation inside the parent `transaction.atomic()`:** rejected — an aggregation failure would roll back the reviewer's submission, trading a recoverable display issue for lost reviewer work. Same trade-off as the score writer at [ADR-0013](0013-dual-write-scores-from-evaluations-and-annotations.md): the parent write must win.
