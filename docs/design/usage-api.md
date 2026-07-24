---
status: active
---

# Usage API

> Design document for a generic, team-scoped **usage-inspection API** under `/api/v2/`.
> Originates from a client request to return "how many messages a user has sent in the current
> calendar month", generalised into a single flexible endpoint that lets a client inspect their
> activity and cost/token usage across configurable dimensions and time windows.
>
> `status: active` — still evolving; ADR extraction is gated off until this flips to `stable`.

## TL;DR

A single endpoint — `GET /api/v2/usage/` — returns team-scoped usage as a set of requested
**metrics** (`messages`, `sessions`, `participants`, `cost`, `tokens`) over a **time window**
(`current_month` shortcut or explicit `start`/`end`, at `total`/`daily`/`weekly`/`monthly`
granularity), optionally **grouped** by `participant`, `chatbot`, or `platform`, and filtered by
the same dimensions. The client's original ask — a participant's human-message count this
calendar month — is one call: `?metric=messages&period=current_month&participant=<public_id>`.

Seven decisions shape the work:

1. **One flexible query endpoint, not a resource tree.** `/api/v2/usage/` with a constrained,
   documented query vocabulary. Generic enough for "inspect their usage data" without new endpoints.
2. **Metrics are additive per request.** `metric` is a repeated query param; the response carries
   one block per requested metric, so activity + cost come back in a single call.
3. **Reuse the existing read paths.** `cost_tracking.services.reporting` for cost/tokens; the
   `DashboardService`/`admin.queries` aggregation patterns for messages/sessions/participants.
   New code lives in `apps/api/v2/usage/services.py`; no coupling to `DashboardService` internals.
4. **`tz` query param, default UTC.** OCS has no team timezone; the client controls month
   boundaries per request. (See [D4](#d4-timezone-via-query-param).)
5. **v2 `chatbot` naming.** Filters, `group_by`, and response fields say `chatbot`, not
   `experiment`, consistent with ADR-0023.
6. **Tokens come from `UsageRecord`.** Single source of truth alongside cost; `prompt`/`completion`
   derive from `service_kind`. Not `Trace`. (See [D6](#d6-tokens-from-usagerecord).)
7. **Authorize on `chat.view_chatmessage`.** Usage is an aggregate over chat messages, so gate it on
   the read permission for the underlying data — not a team-admin/write permission. (See
   [D7](#d7-authorize-on-chatview_chatmessage).)

## Context

### The use case

A client wants to know **how many messages a given user has sent in the current calendar month**.
Rather than build a one-off counter, we generalise: a usage API a client can query to inspect their
own activity and spend. "User" here means a **`Participant`** — the external chat identity, unique
per `(team, platform, identifier)`, with a stable `public_id` UUID and a human-readable `identifier`
(email / phone / `anon:<uuid>`).

### Existing landscape (what we reuse)

Two usage axes already have team-scoped read logic; the API composes them rather than reinventing:

- **Message activity** — `ChatMessage` (`message_type` ∈ `human`/`ai`/`system`). `ChatMessage` is a
  plain `BaseModel` (no direct `team`); scope via `chat__team`. Participant path:
  `ChatMessage → chat → experiment_session → participant`. The `(chat, message_type, created_at)`
  index backs monthly counts. Aggregation already written in `apps/dashboard/services.py`
  (`get_message_volume_data`, `get_user_engagement_data`) and `apps/admin/queries.py`
  (`get_message_stats`, `get_period_totals`).
- **Cost / tokens** — `cost_tracking.UsageRecord` (`BaseTeamModel`), one row per
  `(trace, model, service_kind)`. `apps/cost_tracking/services/reporting.py` already exposes
  team-scoped `cost_summary`, `cost_timeseries`, `costs_by_experiment`, and a `CostFilters`
  dataclass (experiment / participant / platform), with `daily`/`weekly`/`monthly` granularity.
  `service_kind` ∈ `llm_input`, `llm_output`, `llm_cached_input`, `llm_cache_write`; `quantity`
  is the token count and `cost` the priced amount.

Placement follows convention: new API surface lands under `apps/api/v2/` (ADR-0022), team-scoped via
`request.team` from the API key, documented with `@extend_schema`, cursor-paginated. A sibling
precedent is `apps/api/v2/inspect/`.

### Naming note

The v1 session serializer already exposes a `usage` field that means **cost** only
(`SessionUsageSerializer`). The new `/usage/` surface is broader and lives on a different (v2)
surface, so the overlap is acceptable; no v1 change.

## The endpoint

`GET /api/v2/usage/` — an `APIView` (a query, not a resource collection; same shape as `MeView`).

- **Auth:** `IsAuthenticated` + `CanViewUsage` + `TokenHasOAuthResourceScope` with a new `usage`
  scope. API-key and bearer auth work as elsewhere. See [D7](#d7-authorize-on-chatview_chatmessage).
- **Scoping:** every query filters to `request.team`.
- **Docs:** new `"Usage"` tag in `SPECTACULAR_SETTINGS["TAGS"]`; `@extend_schema` on the view.
- **Pagination:** the shared `CursorPagination` when `group_by` is set (breakdown rows); a single
  unpaginated object when ungrouped.

### Query vocabulary

| Param | Values | Notes |
|---|---|---|
| `metric` | `messages`, `sessions`, `participants`, `cost`, `tokens` (repeated param, ≥1, required) | One response block per metric |
| `period` | `current_month`, `previous_month` | Convenience; mutually exclusive with `start`/`end` |
| `start` / `end` | ISO date/datetime | Half-open `[start, end)`; explicit alternative to `period` |
| `granularity` | `total` (default), `daily`, `weekly`, `monthly` | Time-bucketing of results |
| `group_by` | *(none)*, `participant`, `chatbot`, `platform` | Dimension breakdown; paginated when set |
| `participant` | `public_id` UUID | Filter to one participant |
| `participant_identifier` | raw email / phone | Same filter, by the handle the client already knows |
| `chatbot` | `public_id` UUID | Filter to one chatbot |
| `platform` | platform slug | Filter to one channel platform |
| `tz` | IANA name, default `UTC` | Defines calendar boundaries; see [D4](#d4-timezone-via-query-param) |

`group_by` and `granularity` may combine, but that is the cardinality risk. Guard it with pagination
plus a **max window** relative to granularity (e.g. reject `daily` over a multi-year range).

### Metrics → sources

| Metric | Source | Shape |
|---|---|---|
| `messages` | `ChatMessage` grouped by `message_type`, scoped `chat__team`, participant via `chat__experiment_session__participant` | `{human, ai, total}` |
| `sessions` | `ExperimentSession` count | integer |
| `participants` | distinct `Participant` count (only meaningful when **not** grouped by participant) | integer |
| `cost` | `reporting.cost_summary` / `cost_timeseries` with `CostFilters` | `{total, currency}` |
| `tokens` | `UsageRecord.quantity` summed, split by `service_kind` | `{prompt, completion, total}` |

`tokens`: `prompt` = `Sum(quantity)` over `llm_input` (+ `llm_cached_input`), `completion` =
`llm_output`, `total` = all LLM kinds. Derived from the same table as `cost` for consistency
([D6](#d6-tokens-from-usagerecord)).

### Response shape

Grouped (`group_by=participant`), paginated:

```json
{
  "period": {"start": "2026-07-01T00:00:00+00:00", "end": "2026-08-01T00:00:00+00:00", "timezone": "UTC"},
  "group_by": "participant",
  "count": 1,
  "results": [
    {
      "participant": {"public_id": "…", "identifier": "user@example.com", "platform": "web"},
      "messages": {"human": 42, "ai": 40, "total": 82},
      "sessions": 5,
      "cost": {"total": "0.01234000", "currency": "USD"},
      "tokens": {"prompt": 12000, "completion": 8000, "total": 20000}
    }
  ]
}
```

Ungrouped, `total` granularity — a single totals object under `results`. With `granularity != total`
and no `group_by`, `results` is one row per time bucket (each row carries `bucket_start`).

Grouping by participant emits **both** `public_id` and `identifier` per row so clients can map by
either handle. Chatbot rows carry `{public_id, name}`; platform rows carry the slug directly.

`group_by` combined with a finer `granularity` produces **flat rows**: one row per `(group, bucket)`,
each carrying the group identity and a `bucket_start`. Pagination is over the groups (the shared
`CursorPagination`), and each page's groups are expanded to their buckets; the max-window guard bounds
the bucket count. The group universe is the groups **active in the window** (those with at least one
message), so idle groups don't produce all-zero rows.

## Decisions

### D1. One flexible query endpoint

A single `/api/v2/usage/` with a query vocabulary, over a resource tree
(`/participants/{id}/usage`, `/usage/messages`, …) or a fixed summary report. Chosen because the
brief is explicitly "a generic API to inspect usage data": one surface serves current and future
needs without new endpoints, and maps cleanly onto the existing dashboard/cost read paths.

**Rejected:** resource sub-resources (more endpoints, param duplication) and a fixed summary report
(ships fast but every new need is a new endpoint).

### D2. Additive metrics per request

`metric` is a repeated query param (`?metric=messages&metric=sessions`), served by DRF's stock
`MultipleChoiceField` — so the OpenAPI schema advertises the allowed metrics as an enum array rather
than an opaque string, and no custom field is needed. The response carries one block per metric,
which lets "activity + cost/tokens" resolve in one round-trip instead of N calls. A comma-separated
value was considered but rejected: DRF has no built-in for it, and it would forfeit the enum schema.

### D3. Reuse existing read paths; new service module

`apps/api/v2/usage/services.py` holds the query orchestration. Cost/tokens delegate to
`cost_tracking.services.reporting` (already `CostFilters`-parameterised). Message/session/participant
aggregation is written fresh there — small, index-backed querysets — rather than coupling to
`DashboardService`, which returns chart-shaped dicts and runs its own caching.

**Follow-up (own ADR):** longer term, dashboard and API should share one usage-query service so the
two aggregation code paths converge. Out of scope for v1.

### D4. Timezone via query param

OCS has no team-level timezone (`settings.TIME_ZONE = "UTC"`, `USE_TZ = True`). Rather than add a
`Team.timezone` field (model + migration + settings UI), the endpoint accepts a `tz` IANA param,
default `UTC`. Calendar boundaries (`current_month`, `TruncMonth`, etc.) are computed in `tz`; the
returned `period.timezone` echoes it.

**Rejected for now:** a `Team.timezone` field — revisit if teams ask for a persistent default;
it can layer on top (param overrides team default) without breaking this contract.

### D5. v2 `chatbot` naming

Filters, `group_by` values, and response fields use `chatbot`, not `experiment`, consistent with the
v2 rename (ADR-0023). The underlying model is still `Experiment`; the API vocabulary hides that.

### D6. Tokens from `UsageRecord`

Token counts come from `UsageRecord.quantity` (split by `service_kind`), not `Trace.n_*_tokens`.
Keeps tokens and cost on a single source of truth (same rows, same team/participant/chatbot filters,
same time index), so a client's `cost` and `tokens` for a window always reconcile. `Trace` token
fields are left for the admin usage export.

### D7. Authorize on `chat.view_chatmessage`

Usage figures are aggregates over chat messages, so the endpoint requires the read permission for
the underlying data (`chat.view_chatmessage`, granted by the `Chat Viewer` group) via a small
`CanViewUsage` permission class. This gates every auth type — including API keys, which the OAuth
scope check alone does not cover — and mirrors how `ChatbotViewSet` gates on `view_experiment`.

**Rejected:** a team-admin/write permission such as `team.change_team` — that gates *editing the
team*, the wrong axis for a read endpoint. **Also considered:** membership-only (matching the
dashboard UI, which shows this aggregate to any member) — rejected because it leaves API keys
ungated by role and under-gates relative to the sibling v2 read endpoints. As the API grows to
cost/tokens (more sensitive than message counts), a dedicated `view_usage` permission may supersede
this; revisit then.

## Cross-cutting concerns

- **Throttling.** No DRF throttle is configured project-wide; an aggregation endpoint is the right
  place to introduce a scoped throttle class plus the max-window guard from the query vocabulary.
- **Caching.** `DashboardCache` (`BaseTeamModel`, TTL) is available for short-TTL caching of
  expensive grouped/timeseries queries if load warrants; not required for v1.
- **Anonymous participants.** `identifier` may be `anon:<uuid>`; returned as-is. `participant_identifier`
  filtering matches it literally.
- **Empty results.** A participant/window with no activity returns zeroed metric blocks, not 404.
- **`participants` metric under `group_by=participant`** is redundant (always 1) — reject that combo
  with a 400.

## Implementation plan

1. Scaffold `apps/api/v2/usage/` (`views.py`, `serializers.py`, `services.py`, `param_serializers.py`,
   `tests/`), mirroring `apps/api/v2/inspect/`.
2. `param_serializers.py`: a DRF serializer validating the query vocabulary (metric list, mutually
   exclusive period vs start/end, granularity, group_by, filters, `tz`), with the combo guards.
3. `services.py`: `usage_query(team, params) -> UsageResult`. Message/session/participant querysets
   fresh; cost/tokens via `reporting`. Time bucketing honours `tz`.
4. `serializers.py`: response serializers per metric block + the envelope (`period`, `group_by`,
   `results`), with `@extend_schema_field` annotations.
5. `views.py`: `UsageView(APIView)` — validate params, call the service, paginate when grouped,
   `@extend_schema` under the `"Usage"` tag.
6. Register `usage/` in `apps/api/v2/urls.py`; add the `usage` OAuth scope; add the `"Usage"` tag to
   `SPECTACULAR_SETTINGS`.
7. Regenerate `api-schemas/*.yml` via the `spectacular` command.

## Test plan

- **Service:** per-metric aggregation correctness (human vs ai counts, session/participant distinct
  counts, cost/token reconciliation against `UsageRecord`), team isolation, `tz` month boundaries
  (a message at 23:30 UTC on the last of the month lands in the right month under a non-UTC `tz`),
  participant path via both `public_id` and `identifier`.
- **View / params:** `period` vs `start`/`end` exclusivity, granularity + group_by combos, the
  rejected combos (400), pagination on grouped results, auth/scope enforcement, team scoping.
- Use `pytest.mark.parametrize` with readable `id`s for the metric/granularity matrices.
