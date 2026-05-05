# List View Performance — Approach 1

**Status:** Draft
**Date:** 2026-04-30
**Owner:** Simon Kelly

## Problem

On teams with substantial data, four list views are slow:

- **Trace list** (`TraceTableView`) — `pg_stat_statements` shows the main `SELECT trace_trace … ORDER BY timestamp DESC` averaging ~2.8s; the sorted/filtered variant with `SELECT DISTINCT` averages ~10s, and its `COUNT(*)` over `SELECT DISTINCT` averages ~9s.
- **Session list** (`ChatbotSessionsTableView`, `AllSessionsHome`) — pagination `COUNT(*)` averages 350ms with a `LEFT OUTER JOIN channels_experimentchannel` that the view does not appear to need; the `customtaggeditem` prefetch returns ~10M rows over 632 calls (~16K rows per page load).
- **Chatbot list** (`ChatbotExperimentTableView`) — averages ~250ms; four correlated subqueries per row plus a 24h trend query. Smaller hit, but visible.
- **Annotation queue sessions** (`AnnotationQueueSessionsTableView` and its `_json` sibling, `apps/human_annotations/views/queue_views.py:218`) — same shape as the session list, same bottlenecks.

Scale at the largest team: ~500K traces, ~50K sessions, ~1K experiments (including versions).

## Goals & non-goals

This is **Approach 1** of a two-phase plan: low-risk, high-leverage fixes first, measure, then decide whether Approach 2 (keyset pagination, denormalized counters on `Experiment`) is justified.

**In scope:**

- Add missing indexes on `Trace` and `ExperimentSession`.
- Rewrite the unconditional `.distinct()` in `MultiColumnFilter` and convert offending filters to `EXISTS`.
- Remove `team__slug` join in `TraceTableView`.
- Bound the `CustomTaggedItem` prefetch on the session list.
- Investigate and remove the stray `LEFT OUTER JOIN channels_experimentchannel` in the session-list `COUNT`.

**Out of scope (deferred):**

- Keyset pagination on Trace.
- Denormalized aggregate columns on `Experiment` (`session_count`, `participant_count`, `interaction_count`, `last_activity_at`).
- Materialized rollups for trend data.
- `Trace` partitioning / retention policy.
- The team dashboard's aggregate queries (separate spec — they hit the same tables and would benefit from these indexes incidentally).

## Success criteria

Re-pull `pg_stat_statements` after rollout and verify:

- Trace list main query mean execution time drops by ≥80%.
- Session list `COUNT(*) … DISTINCT` total time drops by ≥50%.
- No new duplicate-row regressions in test or staging QA.
- `Trace` / `ExperimentSession` insert latency unchanged within ±10%.

If the gates are met, Approach 1 is complete. Re-evaluate Approach 2 from a fresh baseline.

## Design

### Trace list — indexes & filter cleanup

Add three indexes on `Trace`, all created with `AddIndexConcurrently`:

```python
class Meta:
    indexes = [
        models.Index(
            fields=["team", "-timestamp"],
            name="trace_team_timestamp_idx",
            condition=~Q(status="pending"),  # partial — matches view WHERE clause
        ),
        models.Index(fields=["experiment", "-timestamp"], name="trace_experiment_timestamp_idx"),
        models.Index(fields=["session", "-timestamp"], name="trace_session_timestamp_idx"),
    ]
```

Rationale:

- The partial `(team, -timestamp) WHERE status != 'pending'` matches the view's hot path exactly: `Trace.objects.filter(team=...).exclude(status=PENDING).order_by("-timestamp")`. Index range scan, no sort.
- `(experiment, -timestamp)` covers the most common filter narrowing (filter by chatbot) and also benefits `get_bulk_trend_data` on the chatbot list.
- `(session, -timestamp)` covers session-detail-page lookups and any filter pivoting on session.

We deliberately do **not** add `(team, status, -timestamp)` for now. The partial index handles the default view, and status filtering can use the partial then filter status in-memory. If post-rollout data shows status filtering is heavy and not well-served, we add it then.

Also in `TraceTableView.get_queryset`: replace `team__slug=self.request.team.slug` with `team=self.request.team` to remove a redundant join.

### Session list — pagination COUNT cleanup

Add a composite index on `ExperimentSession`:

```python
models.Index(fields=["team", "-last_activity_at"], name="expsession_team_lastactivity_idx"),
```

Every session list filters by team and orders by `last_activity_at`; this turns the order_by into a tail scan.

Bound the `CustomTaggedItem` prefetch in `ExperimentSessionObjectManager.get_table_queryset`. Today the prefetch fires across the entire filtered queryset, before pagination is applied. Two options:

- **Option A (recommended):** override `get_table_data` on the relevant table views to slice to the visible page first, then attach the prefetch only to the page rows. Mirrors the pattern already used in `ChatbotExperimentTableView.get_table_data` for `get_bulk_trend_data`.
- **Option B (deferred):** drop the prefetch and render tag chips lazily via an HTMX endpoint. Cleaner but bigger; defer to Approach 2 if it comes up.

Option A is the chosen implementation.

The stray `LEFT OUTER JOIN channels_experimentchannel` visible in the pg_stat `COUNT(*)` row is investigated and removed. Tracing the source is an implementation step — likely a single column accessor or `order_by` reference. Not designed in detail here.

The per-row `message_count` Subquery is left as-is. At 50K sessions and ~25 per page, 25 small subqueries on an indexed FK is cheap. Revisit if data says otherwise.

### Filter machinery — make `DISTINCT` conditional

`MultiColumnFilter.apply` (`apps/web/dynamic_filters/base.py:112`) ends with `return queryset.distinct()` unconditionally. Every filtered list pays for it whether or not any active filter actually multiplies rows.

Audit of which filters actually need DISTINCT:

| Filter | Needs DISTINCT? | Notes |
|---|---|---|
| `ParticipantFilter` | No | FK join, no multiplication |
| `TimestampFilter` (most uses) | No | Direct column on session/trace |
| `TimestampFilter("Message Date", chat__messages__created_at)` | **Yes** | One-to-many join through messages |
| `ChatMessageTagsFilter.apply_any_of` / `apply_excludes` | **Yes** | Joins `chat__messages__tags` |
| `ChatMessageTagsFilter.apply_all_of` | No | Already uses `Exists` |
| `VersionsFilter` | No | Array overlap on session column |
| `ChannelsFilter` | No | Direct `platform` column |
| `ExperimentFilter` | No | FK filter |
| `SessionStatusFilter` | No | Direct column |
| `RemoteIdFilter` | No | FK |
| `SessionIdFilter` | No | Direct column |
| `MessageTagsFilter` (trace) | **Yes** | Already calls `.distinct()` itself |
| `MessageTagsFilter` (experiments, used by `ChatMessageFilter`) | **Yes** for `apply_any_of` | `tags__name__in=` joins through M2M |
| `MessageVersionsFilter` (experiments) | **Yes** for `apply_any_of` | Same M2M JOIN, with `tag__category` constraint |
| `TraceStatusFilter`, `ExperimentVersionsFilter` | No | Direct column |

The fix:

1. Remove the unconditional `return queryset.distinct()` from `MultiColumnFilter.apply`.
2. Rewrite the row-multiplying filters as `EXISTS` subqueries (extending the pattern already in `ChatMessageTagsFilter.apply_all_of`):
   - `ChatMessageTagsFilter.apply_any_of` and `apply_excludes`
   - `MessageTagsFilter.apply_any_of`, `apply_all_of`, `apply_excludes` (trace)
   - `MessageTagsFilter` and `MessageVersionsFilter` `apply_any_of` (experiments — used by `ChatMessageFilter`)
   - `TimestampFilter` when its column traverses `chat__messages__*`

`EXISTS` is the structurally correct expression of "rows where a related row matches" — no multiplication, no DISTINCT. Both the page query and the `COUNT(*)` get clean shapes.

Backwards compatibility: this is the only change that could expose duplicate rows if a join-multiplier is missed. Mitigation: per-filter unit tests asserting `apply()` returns no duplicates with fixtures containing multiple messages/tags per session, plus assertions on the emitted SQL (no `DISTINCT`).

This change benefits all four in-scope views plus the annotation-queue-items table as a bonus.

### Chatbot list — small wins only

This view does not go through `MultiColumnFilter` (it uses `similarity_search` and direct `.filter()`). The `SELECT DISTINCT experiment` in pg_stat appears to come from `Experiment.objects.get_all()` or working-version logic — a one-line audit during implementation; if the DISTINCT is from a join we don't need, drop it.

The four correlated subqueries (`session_count`, `participant_count`, `interaction_count`, `last_activity`) are left in place — at 250ms total they are not the bottleneck, and denormalizing is explicitly Approach 2 work. `get_bulk_trend_data` is similarly left alone; it picks up a small free win from `trace_experiment_timestamp_idx`.

## Rollout

Each step is independently shippable. Ship in this order so we can attribute wins per change:

1. `Trace` indexes via `AddIndexConcurrently` (`Migration.atomic = False`).
2. `team__slug` → `team` fix in `TraceTableView` — one-line PR.
3. `ExperimentSession.last_activity_at` composite index.
4. `MultiColumnFilter` DISTINCT removal + `EXISTS` rewrites in offending filters.
5. Bound the `CustomTaggedItem` prefetch on session list.
6. Investigate and fix the stray `experiment_channel` `LEFT JOIN` in the session `COUNT`.

After each step: re-pull `pg_stat_statements` and the Sentry route metrics for `chatbots:table`, `chatbots:sessions-list`, `chatbots:all_sessions_list`, `trace:table`, `human_annotations:queue_sessions_table`. If a change does not move the numbers, pause and re-investigate before continuing.

## Risks

| Risk | Mitigation |
|---|---|
| `CONCURRENTLY` index creation fails or is slow | Use `AddIndexConcurrently` with `Migration.atomic = False`. Monitor `pg_stat_progress_create_index`. Roll back via `DROP INDEX CONCURRENTLY`. |
| Removing global `.distinct()` exposes duplicate rows | Per-filter unit test asserting no duplicates with multi-tag/multi-message fixtures. SQL-shape assertion that emitted query lacks `DISTINCT`. |
| `EXISTS` rewrites change result set in subtle ways | Run the existing tests for each filter operator (`any of`, `all of`, `excludes`) and add new ones covering multi-tag and multi-message fixtures. |
| Partial-index condition drift if a new `TraceStatus` is added | Comment on the index and a regression test exercising non-pending statuses. |
| Planner picks a bad plan | `EXPLAIN ANALYZE` after each migration; `ANALYZE trace_trace;` if needed. `pg_hint_plan` only as last resort — not expected at 500K rows. |
| Slicing in `get_table_data` interacts badly with django-tables2 ordering / sort headers | Test sort-by-each-column on a seeded staging dataset. The chatbot list already uses this pattern, so confidence is high. |

## Measurement

**Before each step:**

- Snapshot `pg_stat_statements` for queries against `trace_trace`, `experiments_experimentsession`, `experiments_experiment`, `annotations_customtaggeditem`. Save calls / mean / total.
- `EXPLAIN (ANALYZE, BUFFERS)` on representative production-shape queries (default view, view + sort, view + filter combinations). Save plans.
- Sentry transaction p95 for the affected URL routes.

**After each step:** repeat. Compare. If a change doesn't move the right numbers, pause.

A markdown file in this PR will collect the before/after numbers per step so we can quote them in the post-rollout review and decide whether Approach 2 is needed.
