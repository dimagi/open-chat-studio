# ADR-0037: Row-multiplying list filters use EXISTS, not a blanket DISTINCT

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-05</p>

## Context

The dynamic-filter framework (`MultiColumnFilter`) backs the trace, session, chatbot, and annotation-queue list views. It previously ended `apply()` with an unconditional `queryset.distinct()` so that any filter traversing a one-to-many relation (a session's chat messages, a message's tags) could not return duplicate outer rows. That blanket `DISTINCT` ran on every filtered list whether or not an active filter actually multiplied rows, and it forced `SELECT DISTINCT` into both the page query and its pagination `COUNT(*)`. On the largest team (~500K traces, ~50K sessions) the sorted/filtered trace query and its count each ran for ~9–10s, dominated by the distinct.

Most filters never multiply rows — they filter on a direct column or a forward FK. Only the relation-traversing ones (message-tag membership, message-date) did, and a `DISTINCT` across the whole result set is an expensive way to correct a problem that only a few filters create.

## Decision

We will remove the unconditional `.distinct()` from `MultiColumnFilter.apply` and make each filter responsible for not multiplying rows. The contract, documented on `apply()`, is: every `ColumnFilter` must yield at most one row per outer-model row. Filters that match against a one-to-many relation express that match as an `Exists` subquery rather than a JOIN — `WHERE EXISTS (related row matches)` selects the outer row once regardless of how many related rows match, so neither the page query nor the `COUNT(*)` needs deduplication.

This applies to the message-date filter and to every tag filter (chat-or-message tags, trace input/output message tags, experiment-version tags), across all three operators (`any of`, `all of`, `excludes`).

## Consequences

- The page query and pagination `COUNT(*)` drop `DISTINCT`, removing the dominant cost on large filtered lists.
- A new filter that traverses a one-to-many relation must use `Exists`; if an author JOINs instead, the safety net is gone and duplicate rows surface. The contract is stated in the `apply()` docstring, and each filter ships a no-duplicate regression test as the guard.
- Removing the blanket distinct silently inflated one filter (`ChatMessageFilter`, slug `message`) whose tag operators still JOINed through the tag M2M; it was rewritten to `Exists` in the same effort. This is the concrete failure mode the contract now guards against.
- `EXISTS` nested inside `Exists` requires a doubled `OuterRef` to reach the outer queryset; this is non-obvious and is called out in an inline comment so it is not "simplified" away.

## Alternatives considered

- **Keep the unconditional `.distinct()`** → rejected: it taxes every filtered list, including the majority of filters that never multiply rows, and it was the measured bottleneck.
- **Make `.distinct()` conditional on whether a row-multiplying filter is active** → rejected: it keeps `DISTINCT` as the dedup mechanism (still slower than `EXISTS` when triggered) and requires each filter to declare a "multiplies rows" flag; `EXISTS` removes the multiplication at the source so no dedup is ever needed.
- **Render tag chips lazily via a separate endpoint to avoid the join entirely** → deferred: larger change, not needed once the filter join is expressed as `EXISTS`.
