---
status: active
---

# Read-only Chatbot Inspection API

> Single canonical design document for the read-only chatbot-inspection API
> tracked in [#3452](https://github.com/dimagi/open-chat-studio/issues/3452),
> with consumer acceptance criteria from [#3458](https://github.com/dimagi/open-chat-studio/issues/3458)
> and the public-ID prerequisite tracked separately in [#3465](https://github.com/dimagi/open-chat-studio/issues/3465).
> The nine questions in [§11](#11-resolved-questions) are now **resolved** (2026-05-29). The
> document is held at `status: active` pending a design review; flip to `stable` once reviewed to
> unlock ADR extraction.

## TL;DR

An external agent ("ACE", automating the Connect Interviews launch + QA workflow) needs to
**read a deployed chatbot's full configuration and assert it is wired correctly** — purely
read-only, via a `read_only` `UserAPIKey` scoped to the team. No writes, no secrets.

The original issue framed this as a **read-only *role*** (enforce `change_*` permissions on
POST as well as GET). We rejected that framing. OCS already has a `UserAPIKey.read_only` flag
enforced by `ReadOnlyAPIKeyPermission`; the real gap is that the API doesn't *expose* enough to
inspect a bot. So the investment goes into **API surface, not a permission/role system**.

Four decisions shape the work:

1. **Invest in the API, not a read-only role.** Reuse the existing `read_only` API-key
   mechanism; build a deep-read endpoint instead.
2. **Introduce URL-path API versioning.** `/api/v1/` freezes today's surface (with the
   existing unversioned routes kept as a permanent alias); `/api/v2/` is where the renamed
   surface and all new endpoints land.
3. **Finish the `experiment` → `chatbot` rename in v2.** The codebase is already mid-rename
   and inconsistent; v2 is the clean break.
4. **Stable public IDs (UUID) for every resource v2 exposes**, landing *before* the inspect
   endpoint so external consumers never dereference numeric DB primary keys.

The endpoint itself — `GET /api/v2/chatbots/{id}/inspect/` — returns a **denormalized,
read-only projection**: a digested pipeline graph, per-node detail, an experiment-level events
block, and an inline, denormalized tree — each node and event embeds the resources it references
(each carrying its `public_id`), with all credentials excluded.

## Context

### The use case

There is a [use-case](https://dimagi.slack.com/archives/C04VBQJ1DL3/p1779856829185219) for
agent-based workflows to inspect a bot's setup in OCS without risking accidental mutation. The
concrete consumer ([#3458](https://github.com/dimagi/open-chat-studio/issues/3458)) is an
external agent that automates the Connect Interviews launch + QA workflow. One of its jobs is to
read a deployed interview bot and assert it is wired correctly: the right router keywords, the
right RAG collection and files, a 24-hour inactivity timeout, and a session-completion custom
action that is actually wired to fire.

### Why not a read-only role

The original issue proposed a read-only user role that would see all views but change nothing,
implemented by checking `change_*` permissions on POST requests (not just `view_*` on GET).
That is a broad, cross-cutting change to OCS's permission model.

OCS already ships a narrower mechanism that satisfies the actual need: a `UserAPIKey.read_only`
flag, enforced by `ReadOnlyAPIKeyPermission` (`apps/api/permissions.py:101`), which blocks unsafe
HTTP methods for read-only keys. An operator can issue a read-only API key **today**. The missing
piece is not enforcement — it is that the API doesn't expose enough of a bot's configuration to
inspect it. So the work is to **expand the read API surface**, leaving the role/permission system
untouched.

### Current API state (May 2026)

Surveying `apps/api/` surfaces three facts that shape the design:

1. **The `experiment` → `chatbot` rename has partially happened, inconsistently.**

   | Surface | Says "experiment" | Says "chatbot" |
   |---|---|---|
   | URL paths | `/api/experiments/`, `/api/openai/<experiment_id>/…` | — |
   | OpenAPI tag | `"Experiments"` (`apps/api/views/experiments.py`) | — |
   | OpenAPI summaries | — | "List Chatbots" / "Retrieve Chatbot" |
   | Operation IDs | `experiment_list`, `experiment_retrieve` | — |
   | Serializer fields | `experiment` (sessions, trigger_bot) | `chatbot_id` (chat widget, ParticipantData) |
   | OAuth scopes | — | `chatbots:read`, `chatbots:interact` (`config/settings.py:910`) |

   The OAuth scope and OpenAPI summaries already anticipate the rename; URLs, operation IDs and
   most serializer fields lag behind.

2. **There is no API versioning today.** No `DEFAULT_VERSIONING_CLASS` in `REST_FRAMEWORK`
   (`config/settings.py:425`), no `/api/v1/` prefix. `SPECTACULAR_SETTINGS["VERSION"]` is schema
   metadata only. The only "version" concept that exists is the *chatbot* version on the OpenAI
   compat endpoint (`/api/openai/<id>/v<int:version>/chat/completions`) — a different concept that
   does not collide.

3. **The rename surface is small.** Two ViewSets (`experiment`, `session`), a handful of
   function-based chat views, and three integration endpoints (`trigger_bot`, `participants`,
   `files`). Renaming is mechanically tractable.

### Strategic framing: read now, write later

These endpoints are read-only for now, but the long-term plan is to add write capabilities to the
APIs. API changes are expensive once in use, so this is the moment to get naming, versioning, and
identifier stability right. The key risk in shipping reads first is that **the read shape
constrains the write shape** — clients assume they can PATCH whatever they GET. The design guards
against this (see [D5](#d5-inspect-is-a-denormalized-read-only-projection-on-a-distinct-url) and
[D4](#d4-stable-public-ids-uuid-for-every-resource-v2-exposes)).

## Goals and non-goals

**Goals.**

- A read-only endpoint that returns a single chatbot's full configuration: identity + version,
  all non-secret settings, a digested pipeline graph, per-node detail, experiment-level events,
  and details of every resource any node or event references (LLM providers/models, collections,
  files, source material, assistants, custom actions, voices, surveys, consent forms, trigger
  actions).
- Satisfy assertions #1–#5 from [#3458](https://github.com/dimagi/open-chat-studio/issues/3458) as
  acceptance criteria.
- Establish v2 naming, URL-path versioning, and stable public IDs so the future write API is not
  boxed in.

**Non-goals.**

- Write/mutate operations (a later v2 addition).
- Cross-team access — team scoping via API key stays intact.
- A read-only *role* or any change to the permission/role model.
- Exposing any encrypted credential blob, API key, OAuth token, or signed file-storage URL.
- A round-trippable export/import format. `/inspect/` is a projection, not a transferable artifact.

## Consumer and acceptance criteria

The verifier ([#3458](https://github.com/dimagi/open-chat-studio/issues/3458)) makes five
assertions. They are the acceptance tests for the payload.

| # | Assertion | Payload requirement |
|---|---|---|
| 1 | Bot exists + is the working/published version we expect | `name`, `external_id`/`public_id`, `version_number` |
| 2 | Pipeline has a router node whose routing keywords match the cohort's schedule | digested graph + per-node detail incl. router `keywords`/`route_key` |
| 3 | An LLM node points at the expected collection (RAG), and the collection has the expected files | node's embedded `collection.files[]` |
| 4 | A 24-hr inactivity `TimeoutTrigger` exists and its action is the completion handler | **experiment-level `events` block** (see [D9](#d9-experiment-level-events-block)) |
| 5 | A "Session Completion" custom action exists and is **wired to fire** | custom action embedded under the node/event that fires it (see [D10](#d10-wiring-is-implicit-in-nesting)) |

Assertion #4 is the one a naive node-only walk would miss: triggers hang off the experiment, not
the pipeline graph.

## Decisions

This section is the ADR-extraction surface. Each decision is independently supersedable and is
written to stand on its own.

### D1 — Invest in API surface, not a read-only role

**Decision.** Do not build a read-only user role enforcing `change_*` permissions on writes.
Instead, reuse the existing `UserAPIKey.read_only` flag (`ReadOnlyAPIKeyPermission`) and invest the
effort in a deep-read API endpoint.

**Context.** The read-only-role approach is a broad change to the permission model; the read-only
API-key mechanism already exists and satisfies the agent-inspection use case once paired with a
richer read endpoint.

**Consequences.** No permission-model churn. Operators issue a `read_only` key today. The cost
moves entirely into building (and maintaining) the inspection surface and its secrets-exclusion
guarantees.

**Alternatives considered.** Read-only role checking `change_*` on POST — rejected as
disproportionate effort for a need already met by `read_only` API keys.

### D2 — URL-path API versioning, v1 frozen / v2 new

**Decision.** Enable DRF `URLPathVersioning` (`ALLOWED_VERSIONS = ["v1", "v2"]`). `/api/v1/`
exposes today's surface, frozen — no new features. The existing unversioned routes
(`/api/experiments/…`) stay as a **permanent alias** of v1 so current callers never break. All new
endpoints (including `/inspect/`) and the renamed surface land under `/api/v2/`. Serve per-version
OpenAPI schemas and Swagger UIs (`/api/v1/schema/`, `/api/v2/schema/`). No deprecation timer on v1
yet — add `Sunset`/`Deprecation` headers later once external adoption is understood.

**Context.** There is no versioning today, and the rename + new endpoints would otherwise break
existing callers if done in place.

**Consequences.** v1 and v2 can share zero code if needed; a version is explicit in any log line
and trivially testable from curl. Cost: two routers, two schemas, two docs pages to maintain.

**Alternatives considered.**

- **Header-based** (`Accept: application/vnd.ocs.v2+json`) — clean URLs, but easy for clients to
  omit and silently fall back to v1; hostile to the "external agent inspects a bot" use case and
  hard to test from a browser/curl.
- **Query param** (`?version=2`) — collides with the chatbot-version `?version=` param on the
  inspect endpoint. Hard reject.

### D3 — Finish the experiment → chatbot rename in v2

**Decision.** v2 uses `chatbot` everywhere: `/api/v2/chatbots/`, operation IDs `chatbot_list` /
`chatbot_retrieve` / `chatbot_inspect`, OpenAPI tag `"Chatbots"`, and serializer field renames
(`experiment` → `chatbot`, `experiment_id` → `chatbot_id`). Sessions nest under the chatbot:
`/api/v2/chatbots/{id}/sessions/`. v1 keeps `experiment` naming, frozen.

**Context.** The rename is already half-done and inconsistent across the API surface; v2 is a clean
break that finishes it without disturbing existing callers.

**Consequences.** The external vocabulary becomes consistent and matches the product's user-facing
"Chatbot" term. Internal model names (`Experiment`) are unaffected — this is an API-surface rename
only. v1 and v2 payloads diverge in field names, which is the intended cost of a versioned break.

**Alternatives considered.** Rename in place (no v2) — rejected because it breaks every existing
caller. Leave the inconsistency — rejected because API names ossify once consumed.

### D4 — Stable public IDs (UUID) for every resource v2 exposes

**Decision.** Every resource model the v2 API surfaces gets a stable opaque `public_id`
(`UUIDField`), landing **before** the inspect endpoint. v2 emits `public_id` for all nested
resource references; numeric DB primary keys never cross the API boundary. Tracked and scoped
separately in [#3465](https://github.com/dimagi/open-chat-studio/issues/3465).

**Context.** Numeric IDs are unstable across environments (staging vs. prod), leak DB internals,
and lock the API in once external consumers dereference them. `Experiment`, `Participant`, and
`ExperimentSession` already have public/external IDs; this extends the established pattern.

**Consequences.** Response payloads are portable across environments and forward-compatible with
the eventual write API (writes will accept the same public IDs reads emit). Cost: a 19-model
migration (additive, three-step nullable → backfill → non-null), a `PublicIdMixin`, and a fresh
UUID assigned per version in `create_new_version`. No existing endpoint changes shape — the work is
purely preparatory.

**Alternatives considered.** Deterministic hashed/slug IDs — more moving parts than a UUID for no
gain here. Numeric IDs in v2 then migrate later — rejected as burning external integrators on IDs
we'd have to deprecate. Human-readable slugs — separate debate, out of scope.

### D5 — `/inspect/` is a denormalized read-only projection on a distinct URL

**Decision.** The inspection payload lives at its own URL — `/api/v2/chatbots/{id}/inspect/` —
separate from the plain `GET /api/v2/chatbots/{id}/`. The plain GET stays minimal (the existing
fields); `/inspect/` is explicitly a denormalized, read-only projection. Future PATCH lands on the
plain resource, never on `/inspect/`.

**Context.** Reads ship before writes. If the rich read shape sat on the canonical resource URL,
clients would assume it is round-trippable and that they can PATCH whatever they GET.

**Consequences.** The signal to consumers is unambiguous: `/inspect/` is a view, not a
representation. The write API is free to take a different (normalized) shape later. Cost: two
read shapes for a chatbot (minimal canonical + rich inspect).

**Alternatives considered.** `/export/` naming — rejected because it implies a transferable,
re-importable artifact, which this is not. Putting the rich shape on the canonical GET — rejected
per the read-constrains-write risk above.

### D6 — Inline nested resource tree (denormalized)

**Decision.** Each node and event embeds the resources it references **inline**, under named keys
(e.g. a node carries `llm_provider`, `collection` objects directly; a `collection` carries its
`files[]`). The response is a self-contained tree read top-to-bottom with no pointer-chasing. Every
embedded resource still carries its `public_id` as `id`, so a consumer that wants to deduplicate can
build its own map keyed by `id`.

**Context.** The primary consumer is an LLM-agent verifier reasoning over the JSON. Locality —
having a node's resources right there beside it — matters more for that consumer than wire-size
minimisation. A resource referenced by many nodes is duplicated, but the same resource is byte-for-
byte identical at every site (same `public_id`), so duplication is recoverable, not lossy.

**Consequences.** Single-pass readability; a node is self-describing. Wiring is implicit in
containment (see [D10](#d10-wiring-is-implicit-in-nesting)). Cost: a shared resource is repeated at
each reference site, so payload size is non-deterministic and grows with fan-out — this amplifies the
size concern noted in [resolved Q9](#11-resolved-questions) (full payload in v1, no `?include=`
filter). Diffing two bots is harder than with a normalised table. The collector must still batch-load
each resource type once to avoid N+1, then inline copies (see [implementation](#track-c-the-inspect-endpoint-depends-on-a-and-b)).

**Alternatives considered.**

- **Flat lookup tables keyed by `public_id`** (nodes carry references, clients dereference) —
  deterministic dedup, predictable size, easy diffing, but the consumer must resolve every reference
  and the payload is less readable in isolation. Rejected in favour of consumer locality.
- **JSON:API compound document** (`{data, included}` with typed relationships) — standardised and
  deduped, but verbose envelope and `{type, id}` linkage boilerplate for what is a read-only
  projection, and the consumer still resolves `included[]`. Rejected.

### D7 — Data-driven node walker via `options_source`

**Decision.** The serializer does not hand-maintain a node-type → reference-field table. It looks up
each node's pydantic class from the registry (`apps/pipelines/nodes/__init__.py`), iterates its
model fields, and uses `json_schema_extra.options_source` (e.g. `OptionsSource.collection`,
`OptionsSource.llm_provider_model`) as the canonical signal that a field is a resource reference.
Fields with an `options_source` are resolved to their resource and **embedded inline** under a named
key (per [D6](#d6-inline-nested-resource-tree-denormalized)); the rest go verbatim into `params`.

**Context.** Node types are added over time; a hand-maintained mapping goes stale silently.

**Consequences.** Adding a new node type requires no serializer change as long as its reference
fields declare an `options_source`. Cost: correctness depends on node authors setting
`options_source` consistently — worth a test that exercises every node type.

**Alternatives considered.** A static node-type → field mapping table — rejected as a maintenance
liability that drifts from reality.

### D8 — Secrets exclusion via per-resource serializers with explicit field lists

**Decision.** Each resource type has its own `ModelSerializer` with an explicit `fields = [...]`
list — never `__all__`. Encrypted `config` blobs (provider API keys, bot tokens, OAuth creds),
signed file-storage URLs, and full `CustomAction.api_schema` are excluded; custom-action schemas
are reduced to a path/operation-only digest. A registry maps resource type → serializer.

**Context.** The endpoint is read by an external agent; a single leaked field is a credential
breach. Adding a field to a model must never silently expose it.

**Consequences.** Adding a model field is safe by default (opt-in exposure). Secret-leak tests
assert that `config` and other excluded keys never appear anywhere in the response JSON. Cost: a
serializer per resource type and a deliberate field-by-field audit.

**Alternatives considered.** `fields = "__all__"` with a denylist — rejected: a new sensitive field
leaks until someone remembers to add it to the denylist.

### D9 — Experiment-level events block

**Decision.** The payload includes a top-level `events` block (peer to `pipeline` and `nodes`) with
`static_triggers[]` and `timeout_triggers[]`, each nesting its `EventAction` (`action_type` +
`params`). This is in addition to the node walk.

**Context.** `StaticTrigger` and `TimeoutTrigger` (`apps/events/models.py`) attach to the
experiment, not the pipeline graph. A node-only walk silently omits them — including the 24-hour
inactivity timeout that is assertion #4 and the single most important thing the Connect Interviews
verifier checks.

**Consequences.** Assertion #4 becomes reachable. `EventLog` entries are excluded (operational
telemetry, not config). `EventAction.params` reference other resources, embedded inline on the action
(per [D6](#d6-inline-nested-resource-tree-denormalized)): `pipeline_start` embeds the referenced
pipeline with its digested graph ([resolved Q2](#11-resolved-questions)); `schedule_trigger` embeds
the `ScheduledMessage` cadence ([resolved Q3](#11-resolved-questions)). Disabled triggers are
included but flagged via `is_active`, so a verifier can assert a trigger *isn't* armed.

**Alternatives considered.** Treating triggers as node resources — rejected: they are not nodes and
do not live in the graph.

### D10 — Wiring is implicit in nesting

**Decision.** With inline nesting ([D6](#d6-inline-nested-resource-tree-denormalized)), a resource's
*wiring* is shown by **where it is embedded**: a custom action nested under a node is wired to that
node; one nested under an event action is wired to that event. No separate `attached_to[]`
back-reference field is emitted — containment is the back-reference.

**Context.** Assertion #5 wants to verify a custom action is *wired to fire*, not merely that it
exists. Under a flat table this needed an explicit reverse-index; under an inline tree the position
already carries that information.

**Consequences.** Assertion #5 is satisfied directly by structure — the verifier finds the custom
action under the node/event that fires it. No reverse-index pass is needed. Cost: to answer "is this
resource wired *anywhere*?" the consumer scans the tree rather than reading one back-reference list;
acceptable, since the tree is the document it already walks.

**Alternatives considered.** Keeping an explicit `attached_to[]` field alongside inline nesting —
rejected as redundant: it restates what containment already encodes. A flat table with a reverse
index — rejected with [D6](#d6-inline-nested-resource-tree-denormalized).

## Endpoint shape

```
GET /api/v2/chatbots/{public_id}/inspect/
GET /api/v2/chatbots/{public_id}/inspect/?version=<n>        # specific published version
GET /api/v2/chatbots/{public_id}/inspect/?version=default    # the default published version
```

- Implemented as `@action(detail=True, methods=["get"])` on the v2 chatbot ViewSet, reusing the
  existing lookup (`public_id`), authentication, team scoping, and OAuth scope (`chatbots`).
- Default (no `version`): the **working** (draft) version, matching the existing
  `get_queryset()` filter (`working_version__isnull=True`).
- Decorated with `extend_schema(operation_id="chatbot_inspect", tags=["Chatbots"], …)`.

### Authentication and permissions

No new mechanism. Reuses what the chatbot ViewSet already has:

| Concern | Mechanism | Location |
|---|---|---|
| API key auth | `ApiKeyAuthentication` / `BearerTokenAuthentication` | `apps/api/authentication.py` |
| Team scoping | `request.team`; queryset filtered by `team__slug` | `apps/api/views/experiments.py` |
| Model perm | `DjangoModelPermissionsWithView` (GET → `view` perm) | `apps/api/permissions.py:126` |
| Read-only key safety | `UserAPIKey.read_only` + `ReadOnlyAPIKeyPermission` | `apps/api/permissions.py:101` |
| OAuth scope | `TokenHasOAuthResourceScope`, scope `chatbots` | `apps/api/views/experiments.py` |

**Resolved:** reuse the existing `view` permission — no dedicated `inspect_chatbot` perm. A
`read_only` key with `view` access can inspect. See [resolved Q1](#11-resolved-questions).

## Response shape

Inline nested tree ([D6](#d6-inline-nested-resource-tree-denormalized)). Each node and event embeds
the resources it references; every embedded resource carries its `public_id` as `id` so a consumer
can dedup client-side if it wants. Credentials are excluded ([D8](#d8-secrets-exclusion-via-per-resource-serializers-with-explicit-field-lists)).

```jsonc
{
  "chatbot": {
    "id": "<public_id>",
    "name": "Customer Support Bot",
    "description": "…",
    "version_number": 0,
    "is_working_version": true,
    "is_default_version": false,
    "version_description": null,
    "team_slug": "acme",
    "settings": {
      // every non-secret field on Experiment, null if unset
      "temperature": 0.7,
      "conversational_consent_enabled": false,
      "citations_enabled": true,
      "voice_response_behaviour": "reciprocal",
      "participant_allowlist": [],
      "tools": ["delete-reminder", "..."]
    },

    // chatbot-level resources embedded inline (null if unset)
    "source_material": { "id": "<pub>", "topic": "Returns policy", "material": "# Returns\n…" },
    "consent_form":    { "id": "<pub>", "name": "Default", "consent_text": "…", "capture_identifier": true },
    "pre_survey":      null,
    "post_survey":     { "id": "<pub>", "name": "CSAT", "url": "https://…" },
    "synthetic_voice": { "id": "<pub>", "name": "Rachel", "language": "English",
                         "voice_provider": { "id": "<pub>", "type": "elevenlabs", "name": "ElevenLabs Prod" } },
    "trace_provider":  null,
    "safety_layers":   [],

    "channels": [                         // ExperimentChannel — secrets stripped (resolved Q8)
      { "platform": "telegram", "name": "Support TG",
        "messaging_provider": { "id": "<pub>", "type": "telegram", "name": "Support TG bot" } }
    ]
  },

  "pipeline": {
    "id": "<pub>",
    "name": "Support flow v3",
    "version_number": 0,
    "graph": {                            // digested — positions stripped, edges kept
      "nodes": [
        {"flow_id": "start-1",  "type": "StartNode",            "label": "Start"},
        {"flow_id": "llm-1",    "type": "LLMResponseWithPrompt", "label": "Classify intent"},
        {"flow_id": "router-1", "type": "RouterNode",           "label": "Route"},
        {"flow_id": "end-1",    "type": "EndNode",              "label": "End"}
      ],
      "edges": [
        {"source": "start-1",  "target": "llm-1",    "source_handle": "output", "target_handle": "input"},
        {"source": "router-1", "target": "end-1",    "source_handle": "branch_a", "target_handle": "input"}
      ]
    }
  },

  "nodes": [
    {
      "flow_id": "llm-1",
      "type": "LLMResponseWithPrompt",
      "label": "Classify intent",
      "params": {                         // non-reference fields, verbatim
        "prompt": "You are…",
        "history_type": "global",
        "tools": []
      },
      // fields with an options_source resolved + embedded inline (D7); null if unset
      "llm_provider":       { "id": "<pub>", "type": "openai", "name": "Prod OpenAI" },
      "llm_provider_model": { "id": "<pub>", "type": "openai", "name": "gpt-4o", "max_token_limit": 128000, "deprecated": false },
      "source_material":    { "id": "<pub>", "topic": "Returns policy", "material": "# Returns\n…" },
      "collection": {
        "id": "<pub>", "name": "Policy docs", "is_index": false,
        "embedding_provider_model": { "id": "<pub>", "type": "openai", "model_name": "text-embedding-3-small" },
        "files": [                        // collection files embedded → assertion #3
          { "id": "<pub>", "name": "returns.pdf", "content_type": "application/pdf", "content_size": 50321, "purpose": "collection" }
        ]
      },
      "collection_indexes": [],
      "custom_actions": [                 // wired-to-this-node by containment → assertion #5 (D10)
        { "id": "<pub>", "name": "Session Completion", "server_url": "https://…",
          "allowed_operations": ["complete_session"],
          "api_schema": { "paths": ["/complete_session"] } }   // path/operation digest only (resolved Q7)
      ]
    }
    // … one entry per node
  ],

  "events": {                             // experiment-level — D9
    "static_triggers": [
      {
        "id": "<pub>",
        "type": "conversation_end",       // StaticTriggerType
        "is_active": true,
        "action": {
          "id": "<pub>", "action_type": "pipeline_start",
          // pipeline_start embeds the referenced pipeline inline (resolved Q2)
          "pipeline": { "id": "<pub>", "name": "Completion flow", "graph": { "nodes": [], "edges": [] } }
        }
      }
    ],
    "timeout_triggers": [
      {
        "id": "<pub>",
        "delay_seconds": 86400,           // 24h inactivity → assertion #4
        "total_num_triggers": 1,
        "trigger_from_first_message": false,
        "is_active": true,
        "action": {
          "id": "<pub>", "action_type": "send_message_to_bot",
          "params": { "message": "Are you still there?" }
          // schedule_trigger actions instead embed: "scheduled_message": { … cadence … } (resolved Q3)
        }
      }
    ]
  }
}
```

A resource referenced from more than one site (e.g. one `LlmProvider` used by several nodes) appears
inline at each site, byte-for-byte identical and sharing the same `id`. Consumers that need to
deduplicate index by `id`.

## Node type → reference field mapping

The walker is data-driven ([D7](#d7-data-driven-node-walker-via-options_source)); this table is
illustrative, not hand-maintained in code. Derived from `apps/pipelines/nodes/nodes.py` and
`apps/pipelines/nodes/mixins.py`.

| Node class | Reference fields |
|---|---|
| `LLMResponse` | `llm_provider_id`, `llm_provider_model_id` |
| `LLMResponseWithPrompt` | above + `source_material_id`, `collection_id`, `collection_index_ids[]`, `custom_actions[]`, `synthetic_voice_id`, `mcp_tools[]` |
| `RouterNode`, `BooleanNode` | `llm_provider_id`, `llm_provider_model_id` |
| `ExtractStructuredData` / `ExtractParticipantData` | `llm_provider_id`, `llm_provider_model_id` |
| `AssistantNode` | `assistant_id` (→ `OpenAiAssistant`, which references `llm_provider_id`, `llm_provider_model_id`) |
| `StaticRouterNode` | none (or `tag_data` depending on `data_source`) |
| `RenderTemplate`, `SendEmail`, `Passthrough`, `StartNode`, `EndNode`, `CodeNode` | none |

## Resource schemas — secrets-exclusion audit

Per [D8](#d8-secrets-exclusion-via-per-resource-serializers-with-explicit-field-lists). Explicit
`fields = [...]` per resource; the column below records the deliberate exclusions.

| Resource | Model location | Excluded (sensitive) |
|---|---|---|
| `LlmProvider`, `VoiceProvider`, `MessagingProvider`, `AuthProvider`, `TraceProvider` | `apps/service_providers/models.py` | `config` (encrypted — API keys / tokens / OAuth creds) |
| `LlmProviderModel`, `EmbeddingProviderModel` | same | — |
| `SyntheticVoice` | `apps/experiments/models.py` | file payload (ID only) |
| `SourceMaterial`, `ConsentForm`, `Survey` | `apps/experiments/models.py` | — |
| `Collection` | `apps/documents/models.py` | — (`openai_vector_store_id` **exposed** — opaque pointer, not a credential; see [resolved Q6](#11-resolved-questions)) |
| `File` | `apps/files/models.py` | **`file` URL** — signed storage URL; never expose |
| `OpenAiAssistant` | `apps/assistants/models.py` | — (`instructions` and `assistant_id` **exposed**; see [resolved Q4/Q5](#11-resolved-questions)) |
| `CustomAction` | `apps/custom_actions/models.py` | **full `api_schema`** — reduce to path/operation digest (OpenAPI docs can embed `securitySchemes` with key examples) |

The audit lives in code as a resource-type → serializer registry, never `__all__`.

## Versioning resolution

- `?version=<n>` → resolve the matching `Experiment` version
  (`working_version=root, version_number=n`); `?version=default` → `is_default_version=True`.
- Published experiments link to a **snapshotted** pipeline and snapshotted FK'd resources. The
  endpoint serializes whichever version the experiment version points at — no special logic; just
  follow the already-snapshotted FKs.
- The response always includes `is_working_version`, `version_number`, `is_default_version` so the
  client knows what it received.
- For working-version requests, triggers come from `experiment.static_triggers.all()` /
  `experiment.timeout_triggers.all()`; for versioned requests, from their own `working_version` FKs.

## Implementation plan

Ordered so each step ships independently and stays reviewable. Three logical tracks, each its own
set of PRs.

### Track A — Public IDs (prerequisite, [#3465](https://github.com/dimagi/open-chat-studio/issues/3465))

1. `PublicIdMixin` in `apps/utils/models.py` + centralised fresh-UUID assignment in
   `VersionsMixin.create_new_version` (assign a new `public_id` when present).
2. Apply to `service_providers` models (7, unversioned) + backfill migration.
3. Apply to versioned models (`SourceMaterial`, `Survey`, `ConsentForm`, `Collection`, `File`,
   `OpenAiAssistant`, `Pipeline`) + backfill migrations.
4. Apply to `CustomAction`, `events.*` (`StaticTrigger`, `TimeoutTrigger`, `EventAction`),
   `SyntheticVoice`.
5. **Scope added by resolved Q3/Q8:** `ScheduledMessage` (surfaced by `schedule_trigger` actions)
   and `ExperimentChannel` (surfaced under `chatbot.channels[]`) now also need a `public_id`.
   Neither is in #3465's original list — that ticket's scope must be expanded to cover them.

Each migration is three-step (nullable `UUIDField` → chunked `RunPython` backfill → drop
nullability). Factories add `public_id = factory.Faker("uuid4")`.

### Track B — v2 routing + frozen v1

6. Enable `URLPathVersioning`; `ALLOWED_VERSIONS = ["v1", "v2"]`. Restructure `apps/api/urls.py`
   under `path("api/<str:version>/", …)`. Wire existing routes under `/api/v1/` and keep
   `/api/experiments/…` as a permanent alias.
7. v2 chatbot + session ViewSets: rename surface (`chatbots`, `chatbot_id`, `chatbot_*` operation
   IDs, `"Chatbots"` tag), nest sessions under chatbots. Per-version OpenAPI schemas + Swagger UIs.

### Track C — the `/inspect/` endpoint (depends on A and B)

8. Add the `inspect` action to the v2 chatbot ViewSet (`extend_schema(operation_id="chatbot_inspect")`).
9. Inspect serializers in a new module (e.g. `apps/api/serializers_inspect.py`): top-level
   `ChatbotInspectSerializer` + per-resource `ModelSerializer`s with explicit `fields`.
10. Pipeline node walker (e.g. `apps/pipelines/inspect.py`): walk `pipeline.node_set.all()`, look up
    each pydantic class via the registry, classify fields by `options_source`. Non-reference fields →
    `params`; for each reference field record `(field_name, resource_type, id)` and accumulate
    `resource_refs: dict[ResourceKey, set[id]]` so the collector can pre-load in batch.
11. Events serializer (e.g. `apps/events/api_serializers.py`): `StaticTrigger` + `TimeoutTrigger`
    with nested `EventAction`; record event-referenced resources (`pipeline_start` → pipeline,
    `schedule_trigger` → scheduled message) the same way. Channels are read from `experiment.channels`
    and their `messaging_provider` added to `resource_refs`.
12. Resource collector + inliner: batch-load each resource type once
    (`Model.objects.filter(id__in=…)` with `select_related`/`prefetch_related` — **one query per
    type, no N+1**, regardless of how many sites reference it), serialize through the secret-safe
    serializers into an `id`-keyed map, then **inline** the serialized object at each reference site
    under its named key ([D6](#d6-inline-nested-resource-tree-denormalized)). Duplication across
    sites is by design. Covers providers, collections/files, source material, consent/surveys,
    assistants, custom actions, messaging providers, event-referenced pipelines, and scheduled
    messages. (Batch-loading is the N+1 guard; inlining copies from the in-memory map, not the DB.)
13. Tests (`apps/api/tests/test_chatbots_inspect.py`) — see below.
14. OpenAPI schema regeneration + verification; docs page describing payload shape and the
    secrets-exclusion policy.

### Test plan

- **Auth:** anonymous → 401; wrong team → 404; `read_only` API key → 200.
- **Versioning:** working version by default; `?version=N` returns the right snapshot;
  `?version=default` resolves the default published version.
- **Node coverage:** a pipeline exercising each node type verifies the `options_source` split.
- **Secret-leak:** for each provider, assert `config` (and every other excluded key) appears
  nowhere in the response JSON (`assertNotIn` against the serialized payload).
- **Acceptance #1–#5** (from [#3458](https://github.com/dimagi/open-chat-studio/issues/3458)):
  identity; router keywords; node's embedded `collection.files[]` inventory; 24-hr `TimeoutTrigger` +
  its embedded action in the `events` block; custom action embedded under the node/event that fires it.
- **Inline shape:** a resource referenced by two nodes appears (identically) under both; assert the
  duplicated objects share the same `id`.

## 11. Resolved questions

All resolved 2026-05-29. Recorded here so the rationale survives into ADR extraction.

1. **Permission granularity** → **reuse the existing `view` permission.** No dedicated
   `inspect_chatbot` perm; a `read_only` key with `view` access can inspect. A least-privilege
   inspect-only perm can be added later if a concrete need appears.
2. **`EventAction.params` for `pipeline_start`** → **embed the referenced pipeline.** The
   `pipeline_start` action embeds the referenced pipeline (with its digested graph) inline, so a
   verifier can inspect trigger-launched flows without a second request.
3. **`schedule_trigger` / `ScheduledMessage`** → **include the cadence.** The `schedule_trigger`
   action embeds the `ScheduledMessage` cadence inline. This expands the public-ID prerequisite:
   `ScheduledMessage` must gain a `public_id` (it was explicitly deferred in #3465 — that scope now
   grows).
4. **`OpenAiAssistant.instructions`** → **expose as-is.** Prompt text the verifier may want to
   assert; accepted residual risk that a team could embed a secret in a prompt.
5. **`OpenAiAssistant.assistant_id`** → **expose.** Opaque OpenAI-side ID, not a credential.
6. **`Collection.openai_vector_store_id`** → **expose.** Treated as an opaque pointer rather than a
   credential. (Note: this went *against* the initial lean to exclude — recorded deliberately.)
7. **`CustomAction.api_schema`** → **path/operation digest.** Strip to operation IDs, paths, and
   summaries; never expose the raw schema (it can embed `securitySchemes` with key examples).
8. **Channels** → **include, secrets stripped.** Surface `ExperimentChannel` under
   `chatbot.channels[]`, each embedding its `messaging_provider` inline; strip tokens from
   `extra_data`. This also expands the public-ID prerequisite: `ExperimentChannel` must gain a
   `public_id` (not in #3465's original list).
9. **Response size** → **ship the full payload in v1.** No `?include=` selective-expansion filter
   for now; revisit only if a real consumer hits size problems.

### Knock-on effects for #3465

Resolutions Q3 and Q8 add two models to the public-ID prerequisite that
[#3465](https://github.com/dimagi/open-chat-studio/issues/3465) does not currently cover:
`ScheduledMessage` and `ExperimentChannel`. That ticket's scope (and the Track A migration set)
must be updated to include them before the inspect endpoint can emit their references.

## Related issues

- [#3452](https://github.com/dimagi/open-chat-studio/issues/3452) — parent: read-only bot
  inspection API (this design).
- [#3458](https://github.com/dimagi/open-chat-studio/issues/3458) — consumer acceptance criteria
  (the ACE verifier).
- [#3465](https://github.com/dimagi/open-chat-studio/issues/3465) — public-ID prerequisite
  (Track A); depends on landing first. **Scope must expand** to add `ScheduledMessage` and
  `ExperimentChannel` per [resolved Q3/Q8](#knock-on-effects-for-3465).
