---
status: active
---

# Session/Message Sync API (v2)

> Design doc for `GET /api/v2/sessions/`, `GET /api/v2/sessions/{id}/` and `GET /api/v2/messages/`:
> read endpoints for consumers (data warehouse, BI tool, CRM) that sync session and message data
> incrementally instead of re-reading everything on each pull. From #3904.

## TL;DR

Two flat, team-scoped collections ([ADR-0072](../adr/0072-flat-routes-for-session-and-message-sync.md))
paged by an exact `(updated_at, id)` keyset cursor that is returned on every page, including the
last, so a consumer stores it and resumes from it on the next pull. Rows younger than a short settle
window are held back so a late-committing write is not skipped. Every write to a field the API
returns has to move `updated_at`; partial saves and tag changes did not, and are fixed first.

## Context

### The use case

A client keeps an external system in sync with session and message data. v1's
`GET /api/v1/sessions/` cannot do this: no `updated_at` filter, no flat message list (messages only
through `GET /sessions/{id}/`), and pagination ordered by `created_at`, so an edited row never
resurfaces.

### What exists and is reused

- **Team-export API** (`apps/api/export/`): team-admin, API-key endpoints that page every resource
  by an exact `(updated_at, id)` keyset for the team migration tool. Its cursor helper and keyset
  filter are what this API needs. The endpoints themselves are not reused: they return raw model
  rows for a migration tool, sit behind team-admin rather than an OAuth scope, and return no cursor
  on the last page.
- **Indexes**: `expsession_updated_at_id_idx` and `chatmessage_updated_at_id_idx` already cover
  `(updated_at, id)` ordering on both tables. No migration.
- **Scope**: `sessions:read` is declared in `OAUTH_CLIENT_CREDENTIALS_SCOPES` and unused.
- **Permission shape**: `apps/api/v2/usage/permissions.py::CanViewUsage` grants client-credentials
  tokens through the OAuth scope and checks a Django permission for everything else. The same shape
  applies with `experiments.view_experimentsession`.
- **Throttling**: `APIRateThrottle` (`api` scope) already applies to every DRF view.

## Decisions

| Decision | Notes |
|---|---|
| D1: flat routes | [ADR-0072](../adr/0072-flat-routes-for-session-and-message-sync.md) |
| D2: exact `(updated_at, id)` keyset cursor, returned on every page | shared with the team-export API |
| D3: settle window on the newest rows | protects against late commits |
| D4: writes to returned fields move `updated_at` | partial saves and tags |
| D5: one scope, `sessions:read`, for both collections | already declared |
| D6: filters applied directly in `get_queryset()` | not through `ExperimentSessionFilter` |
| D7: `/messages/` takes `participant` | answers the open question on the issue |
| D8: IDs follow [ADR-0026](../adr/0026-identify-resources-by-primary-key.md) | public identifier where one exists |
| D9: `metadata` on messages | matches v1 |
| D10: attachments and tags batch-loaded per page | no per-row queries |
| D11: machine tokens read only the chatbots they reach | [ADR-0056](../adr/0056-client-credentials-applications-name-their-chatbots.md) |

### D2: Exact keyset cursor, returned on every page

**Decision.** Order by `(updated_at, id)` ascending and page with the keyset filter
`updated_at > t OR (updated_at = t AND id > i)`. The response always carries `cursor` (the last
row's keyset, or the request's cursor when the page is empty) and `has_more`. The cursor helper and
keyset filter move out of `apps/api/export/views.py` into a shared module used by both APIs.

**Context.** DRF's `CursorPagination` positions on the first ordering field plus an offset, so many
rows sharing an `updated_at` page by offset, and its last page has `next: null`. A sync consumer that
has caught up then holds no pointer to resume from. The issue's `next_since` timestamp is not a safe
substitute: resuming from a bare timestamp either repeats or drops rows that share it.

**Consequences.** A consumer stores one opaque cursor and passes it on every pull, forever. `has_more`
tells it whether to keep paging now or wait for the next cycle. No `count` field: counting a whole
team's messages on each poll costs more than the page.

**Alternatives considered.** DRF `CursorPagination` with `("updated_at", "pk")`: rejected, offset
paging within a timestamp and no resume pointer at the end. A `since` timestamp alone: rejected,
loses or repeats rows at the boundary.

### D3: Settle window on the newest rows

**Decision.** Exclude rows whose `updated_at` is newer than `now() - SYNC_SETTLE_SECONDS` (60).

**Context.** `updated_at` is stamped when Django saves the row, not when its transaction commits. A
transaction that stamps at 12:00:00.100 and commits at 12:00:00.900 is invisible to a pull at
12:00:00.500; if that pull's cursor ends at 12:00:00.400 the next pull starts past it and the row is
never sent.

**Consequences.** Data reaches consumers up to a minute late, which suits ETL. A transaction open for
longer than the window can still be missed; session and message writes are short.

**Alternatives considered.** Document an overlap for clients to apply themselves: rejected, a keyset
cursor cannot be rewound by a client and most would not do it. Transaction-id based change capture:
rejected, far more machinery than a sync API over two tables needs.

### D4: Writes to returned fields move `updated_at`

**Decision.** Every write to a field this API returns sets `updated_at`. Two kinds of write did not:

- `save(update_fields=[...])` (Django writes an `auto_now` field only when `update_fields` names it).
  Eight such saves of session `state`, `status` and `ended_at`, and message `metadata`, existed.
  `UpdatedAtOnPartialSaveMixin` (`apps/utils/models.py`) on `ExperimentSession` and `ChatMessage`
  adds `updated_at` to every partial save, so new call sites are covered too.
- Tag changes write `CustomTaggedItem` rows and never touch the tagged row. Adding or removing a tag
  on a message, or on a session's chat, sets that message's or session's `updated_at`, through
  `touch_tagged_object` in `apps/experiments/services.py`: a receiver on the tag row's save and delete,
  plus an explicit call after evaluator tag rules `bulk_create` their rows, which sends no signal.

**Context.** The cursor only sees what moves `updated_at`. Each gap above is a change a consumer would
never receive.

**Consequences.** The team-export API, which pages by the same column, picks up these changes too. A
`QuerySet.update()` on these models must set `updated_at` itself.

**Alternatives considered.** The mixin on `BaseModel`, for every model: rejected for this work, it
changes write behaviour across the whole codebase. Fixing each call site: rejected, the next partial
save would miss it again. Leave tags out of the
response: rejected, tags are in the issue's field list and a common sync filter.

### D5: One scope, `sessions:read`

**Decision.** `required_scopes = ["sessions:read"]` on all three endpoints.

**Context.** The scope is declared and unused. Messages are read in the context of their sessions.

**Alternatives considered.** A separate `messages:read` scope: rejected, no resource here splits
sub-resources by scope, and nothing lets a client read messages without being trusted with their
sessions.

### D6: Filters in `get_queryset()`

**Decision.** `chatbot`, `participant`, `platform` and `tags` are plain queryset filters in each view.
The platform filter and field are named `platform`, as in the v1 sessions API and the v2 usage API,
rather than the issue's `channel`.

**Context.** `ExperimentSessionFilter` serves the filter widget's `f_<column>` / `op_<column>` wire
format and HTML UI, not a public `?participant=<id>` contract.

**Alternatives considered.** Adapt `ExperimentSessionFilter`: rejected, couples a public API contract
to an internal widget's format for less code saved than it costs.

### D7: `/messages/` takes `participant`

**Decision.** `participant` (`public_id`) filters messages as it does sessions.

**Context.** The issue asks how to read one participant's messages without first finding their
sessions.

**Alternatives considered.** A separate `participant_identifier` string lookup: rejected, `participant`
covers the case with one parameter.

### D8: IDs follow ADR-0026

**Decision.** `session.id` is `external_id`; `chatbot_id` is `experiment.public_id`; `participant_id`
is `participant.public_id`; `message.id` is the primary key.

**Context.** [ADR-0026](../adr/0026-identify-resources-by-primary-key.md): reuse an existing public
identifier, primary key otherwise. Messages have none, and the endpoint is authenticated and
team-scoped.

### D9: `metadata` on messages

**Decision.** `metadata` is a message field, minus the keys v1 strips as internal
(`ChatMessageMetadataKeys.internal_keys()`).

**Context.** v1's `MessageSerializer` exposes it; `compression_marker` lives there (#4012).

### D10: Attachments and tags batch-loaded per page

**Decision.** One query per page each for attachments (`ChatMessage.prefetch_attached_files`),
message tags (`prefetch_related("tags")`) and session tags (`attach_chat_tagged_items`), not per row.

**Context.** `get_attached_files()` queries per call. Fine for one session's messages, not for a page
of up to 1000.

### D11: Machine tokens read only the chatbots they reach

**Decision.** A client-credentials token whose application lacks `all_data` reads only the sessions
and messages of its `allowed_chatbots`. Every other caller reads the whole team.

**Context.** [ADR-0056](../adr/0056-client-credentials-applications-name-their-chatbots.md): the scope
says what a machine token may do, the allowlist which chatbots it may do it to, and both must agree.
Source material and consent forms stay readable team-wide only because they belong to no chatbot;
sessions and messages each belong to one.

**Alternatives considered.** Team-wide reads for every token, as the v2 chatbot list allows: rejected,
conversation content is the data the allowlist exists to fence.

## Endpoints

```
GET /api/v2/sessions/
GET /api/v2/sessions/{id}/
GET /api/v2/messages/
```

### `GET /api/v2/sessions/`

| Param | Type | Notes |
|---|---|---|
| `chatbot` | UUID | `public_id` |
| `participant` | UUID | `public_id` |
| `platform` | slug | e.g. `web`, `whatsapp` |
| `tags` | string | tag name; repeat for any-of |
| `cursor` | opaque | from the previous response |
| `limit` | int | default 100, max 1000 |

| Field | Source |
|---|---|
| `id` | `external_id` |
| `chatbot_id` | `experiment.public_id` |
| `participant_id` | `participant.public_id` |
| `platform` | direct |
| `status`, `state`, `tags` | direct |
| `created_at`, `updated_at`, `ended_at` | direct |

### `GET /api/v2/sessions/{id}/`

The session fields above plus `messages[]` (the `/messages/` shape), unpaginated as in v1.

### `GET /api/v2/messages/`

| Param | Type | Notes |
|---|---|---|
| `chatbot` | UUID | `public_id` |
| `session` | UUID | `external_id` |
| `participant` | UUID | `public_id` |
| `cursor`, `limit` | | as above |

| Field | Source |
|---|---|
| `id` | primary key |
| `session_id` | `chat.experiment_session.external_id` |
| `role` | from `message_type`, as v1 |
| `content`, `metadata`, `tags`, `attachments[]` | see D9, D10 |
| `created_at`, `updated_at` | direct |

### Response envelope

```json
{"cursor": "eyJ1cGRhdGVkX2F0Ijog...", "has_more": false, "results": []}
```

## Delivery

1. This doc, ADR-0072, and the D4 fixes: the writes that skip `updated_at`, each with a test.
2. `GET /api/v2/sessions/`: shared keyset module (D2), settle window (D3), permission and scope
   (D5), filters (D6), serializer (D8).
3. `GET /api/v2/messages/`: filters (D7), metadata (D9), batch loading (D10).
4. `GET /api/v2/sessions/{id}/` with embedded messages.

### Test plan

- First pull, resumed pull, and an empty pull that returns the same cursor.
- Rows sharing one `updated_at` across a page boundary: none skipped, none repeated.
- A row edited after the consumer passed it reappears on the next pull.
- A row newer than the settle window is held back, then returned.
- Each D4 path moves `updated_at`.
- Each filter, team scoping, and the scope gate per auth type.
- Query count per page is constant in the page size (guards D10).

## Related issues

- [#3904](https://github.com/dimagi/open-chat-studio/issues/3904): origin of this design.
- [#4012](https://github.com/dimagi/open-chat-studio/issues/4012): `compression_marker` in message metadata.
