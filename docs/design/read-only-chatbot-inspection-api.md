---
status: active
---

# Read-only Chatbot Inspection API

> Single canonical design document for the read-only chatbot-inspection API
> tracked in [#3452](https://github.com/dimagi/open-chat-studio/issues/3452),
> with consumer acceptance criteria from [#3458](https://github.com/dimagi/open-chat-studio/issues/3458)
> and the public-ID prerequisite tracked separately in [#3465](https://github.com/dimagi/open-chat-studio/issues/3465).
> While this document has `status: active`, ADR extraction is gated off — the open
> questions in [§11](#11-open-questions) are still moving. Flip to `stable` before extracting.

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
block, and flat resource lookup tables keyed by public ID, with all credentials excluded.

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
against this (see [D5](#d5--inspect-is-a-denormalized-read-only-projection-on-a-distinct-url) and
[D4](#d4--stable-public-ids-uuid-for-every-resource-v2-exposes)).

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
| 3 | An LLM node points at the expected collection (RAG), and the collection has the expected files | node detail + `collections[id].file_ids` → flat `files` table |
| 4 | A 24-hr inactivity `TimeoutTrigger` exists and its action is the completion handler | **experiment-level `events` block** (see [D9](#d9--experiment-level-events-block)) |
| 5 | A "Session Completion" custom action exists and is **wired to fire** | custom action detail + `attached_to[]` (see [D10](#d10--attached_to-wiring-back-references)) |

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

### D6 — Flat resource lookup tables keyed by public ID

**Decision.** Resources are flattened into top-level lookup tables (`resources.llm_providers`,
`resources.collections`, `resources.files`, …), keyed by `public_id`. Nodes and events carry only
references into these tables; clients dereference.

**Context.** The same resource (e.g. one `LlmProvider`) is frequently referenced by many nodes.

**Consequences.** Deterministic deduplication, predictable payload size, and easy diffing for an
agent comparing two bots. Cost: clients must dereference rather than read inline.

**Alternatives considered.** Nesting resources inline under each node — rejected for duplication and
non-deterministic size; harder to diff.

### D7 — Data-driven node walker via `options_source`

**Decision.** The serializer does not hand-maintain a node-type → reference-field table. It looks up
each node's pydantic class from the registry (`apps/pipelines/nodes/__init__.py`), iterates its
model fields, and uses `json_schema_extra.options_source` (e.g. `OptionsSource.collection`,
`OptionsSource.llm_provider_model`) as the canonical signal that a field is a resource reference.
Fields with an `options_source` go into `references`; the rest go verbatim into `params`.

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
telemetry, not config). `EventAction.params` for `pipeline_start`/`schedule_trigger` reference other
resources, normalized into the resource tables (see open questions). Disabled triggers are included
but flagged via `is_active`, so a verifier can assert a trigger *isn't* armed.

**Alternatives considered.** Treating triggers as node resources — rejected: they are not nodes and
do not live in the graph.

### D10 — `attached_to[]` wiring back-references

**Decision.** Resources whose *wiring* matters as much as their existence (custom actions,
synthetic voices, source material, collections, assistants) carry an `attached_to[]` field listing
where they are referenced — e.g. `{ "kind": "node", "flow_id": "llm-3" }`. Computed by a single
in-memory reverse-index pass after the node + event walks; no extra DB queries.

**Context.** Assertion #5 wants to verify a custom action is *wired to fire*, not merely that it
exists.

**Consequences.** Verifier-style consumers can assert wiring cheaply. Cost: one reverse-index pass
over the already-loaded payload.

**Alternatives considered.** Only listing resources by existence — rejected: leaves assertion #5
partially unmet.

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

A dedicated `inspect_chatbot` model permission (vs. reusing the existing `view` perm) is an open
question — see [§11](#11-open-questions).

## Response shape

Nodes and events carry only `public_id` references; resources are flat top-level tables.

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
    "references": {                       // FK pointers from the chatbot itself (public IDs)
      "pipeline_id": "<pub>",
      "source_material_id": "<pub>",
      "consent_form_id": "<pub>",
      "pre_survey_id": null,
      "post_survey_id": "<pub>",
      "synthetic_voice_id": "<pub>",
      "voice_provider_id": "<pub>",
      "trace_provider_id": null,
      "safety_layer_ids": []
    }
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
      "references": {                     // fields with an options_source (public IDs)
        "llm_provider_id": "<pub>",
        "llm_provider_model_id": "<pub>",
        "source_material_id": "<pub>",
        "collection_id": "<pub>",
        "collection_index_ids": ["<pub>"],
        "custom_action_ids": ["<pub>", "<pub>"]
      }
    }
    // … one entry per node
  ],

  "events": {                             // experiment-level — D9
    "static_triggers": [
      {
        "id": "<pub>",
        "type": "conversation_end",       // StaticTriggerType
        "is_active": true,
        "action": { "id": "<pub>", "action_type": "pipeline_start", "params": { "pipeline_id": "<pub>" } }
      }
    ],
    "timeout_triggers": [
      {
        "id": "<pub>",
        "delay_seconds": 86400,           // 24h inactivity → assertion #4
        "total_num_triggers": 1,
        "trigger_from_first_message": false,
        "is_active": true,
        "action": { "id": "<pub>", "action_type": "send_message_to_bot", "params": { "message": "Are you still there?" } }
      }
    ]
  },

  "resources": {                          // flat tables, keyed by public_id
    "llm_providers":        { "<pub>": {"id": "<pub>", "type": "openai", "name": "Prod OpenAI"} },
    "llm_provider_models":  { "<pub>": {"id": "<pub>", "type": "openai", "name": "gpt-4o", "max_token_limit": 128000, "deprecated": false} },
    "voice_providers":      { "<pub>": {"id": "<pub>", "type": "elevenlabs", "name": "ElevenLabs Prod"} },
    "synthetic_voices":     { "<pub>": {"id": "<pub>", "name": "Rachel", "language": "English", "voice_provider_id": "<pub>"} },
    "source_material":      { "<pub>": {"id": "<pub>", "topic": "Returns policy", "material": "# Returns\n…"} },
    "consent_forms":        { "<pub>": {"id": "<pub>", "name": "Default", "consent_text": "…", "capture_identifier": true} },
    "surveys":              { "<pub>": {"id": "<pub>", "name": "CSAT", "url": "https://…"} },
    "collections":          { "<pub>": {"id": "<pub>", "name": "Policy docs", "is_index": false, "file_ids": ["<pub>", "<pub>"]} },
    "embedding_provider_models": { "<pub>": {"id": "<pub>", "type": "openai", "model_name": "text-embedding-3-small"} },
    "files":                { "<pub>": {"id": "<pub>", "name": "returns.pdf", "content_type": "application/pdf", "content_size": 50321, "purpose": "collection"} },
    "assistants":           { },          // OpenAiAssistant entries when AssistantNode is used
    "custom_actions":       { "<pub>": {"id": "<pub>", "name": "Session Completion", "server_url": "https://…", "allowed_operations": ["complete_session"], "attached_to": [{"kind": "node", "flow_id": "llm-3"}]} },
    "safety_layers":        { },
    "tags":                 { }
  }
}
```

## Node type → reference field mapping

The walker is data-driven ([D7](#d7--data-driven-node-walker-via-options_source)); this table is
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

Per [D8](#d8--secrets-exclusion-via-per-resource-serializers-with-explicit-field-lists). Explicit
`fields = [...]` per resource; the column below records the deliberate exclusions.

| Resource | Model location | Excluded (sensitive) |
|---|---|---|
| `LlmProvider`, `VoiceProvider`, `MessagingProvider`, `AuthProvider`, `TraceProvider` | `apps/service_providers/models.py` | `config` (encrypted — API keys / tokens / OAuth creds) |
| `LlmProviderModel`, `EmbeddingProviderModel` | same | — |
| `SyntheticVoice` | `apps/experiments/models.py` | file payload (ID only) |
| `SourceMaterial`, `ConsentForm`, `Survey` | `apps/experiments/models.py` | — |
| `Collection` | `apps/documents/models.py` | `openai_vector_store_id` (treat as sensitive — open question) |
| `File` | `apps/files/models.py` | **`file` URL** — signed storage URL; never expose |
| `OpenAiAssistant` | `apps/assistants/models.py` | `assistant_id` (OpenAI-side ID — open question) |
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

Each migration is three-step (nullable `UUIDField` → chunked `RunPython` backfill → drop
nullability). Factories add `public_id = factory.Faker("uuid4")`.

### Track B — v2 routing + frozen v1

5. Enable `URLPathVersioning`; `ALLOWED_VERSIONS = ["v1", "v2"]`. Restructure `apps/api/urls.py`
   under `path("api/<str:version>/", …)`. Wire existing routes under `/api/v1/` and keep
   `/api/experiments/…` as a permanent alias.
6. v2 chatbot + session ViewSets: rename surface (`chatbots`, `chatbot_id`, `chatbot_*` operation
   IDs, `"Chatbots"` tag), nest sessions under chatbots. Per-version OpenAPI schemas + Swagger UIs.

### Track C — the `/inspect/` endpoint (depends on A and B)

7. Add the `inspect` action to the v2 chatbot ViewSet (`extend_schema(operation_id="chatbot_inspect")`).
8. Inspect serializers in a new module (e.g. `apps/api/serializers_inspect.py`): top-level
   `ChatbotInspectSerializer` + per-resource `ModelSerializer`s with explicit `fields`.
9. Pipeline node walker (e.g. `apps/pipelines/inspect.py`): walk `pipeline.node_set.all()`, look up
   each pydantic class via the registry, split fields by `options_source` into `params` vs
   `references`; return `(nodes_payload, resource_refs: dict[ResourceKey, set[id]])`.
10. Events serializer (e.g. `apps/events/api_serializers.py`): `StaticTrigger` + `TimeoutTrigger`
    with nested `EventAction`; feed event-referenced resources into `resource_refs`.
11. Resource collector: batch-load each resource type (`Model.objects.filter(id__in=…)` with
    `select_related`/`prefetch_related` — **one query per type, no N+1**), serialize through the
    secret-safe serializers, key by `public_id`.
12. `attached_to[]` reverse-index pass over the in-memory node/event payloads (no extra queries).
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
  identity; router keywords; collection → files inventory; 24-hr `TimeoutTrigger` + its action in
  the `events` block; custom action with `attached_to`.

## 11. Open questions

1. **Permission granularity.** A dedicated `inspect_chatbot` model permission (least-privilege agent
   tokens) vs. reusing the existing `view` permission. Lean: reuse `view` unless least-privilege is
   a stated requirement.
2. **`EventAction.params` for `pipeline_start`.** The referenced pipeline may not be the chatbot's
   primary pipeline. Emit a reference + add it to a top-level `pipelines{}` table (and serialize its
   graph too), or reference-only? Lean: reference + resource-table entry, matching the flat-table
   convention.
3. **`ScheduledMessage` triggers** (`schedule_trigger` action). Include the scheduling cadence in
   resources, or out of scope? Lean: include — verifiers may assert cadence. ([#3465](https://github.com/dimagi/open-chat-studio/issues/3465)
   leaves `ScheduledMessage` public-ID out of scope until this is decided.)
4. **`OpenAiAssistant.instructions`** — full prompt text; usually not sensitive but could embed
   keys in some teams' configs. Expose as-is or sanitize? Lean: expose.
5. **`OpenAiAssistant.assistant_id`** (OpenAI-side ID) — opaque ID, not a credential. Expose? Lean:
   expose, flagged for review.
6. **`Collection.openai_vector_store_id`** — credential or opaque ID? Decides exposure.
7. **`CustomAction.api_schema`** — path/operation digest (recommended) vs. full schema vs. nothing.
8. **Channels.** Surface `ExperimentChannel` entries (platform, name, `extra_data` minus
   credentials) under `chatbot.channels[]` with `messaging_providers` in resources? Lean: include
   with secrets stripped — a deployment audit wants to know how the bot is exposed.
9. **Response size.** Large bots produce heavy payloads. A `?include=` selective-expansion filter is
   out of scope for v1 of the endpoint; revisit if size becomes a problem.

## Related issues

- [#3452](https://github.com/dimagi/open-chat-studio/issues/3452) — parent: read-only bot
  inspection API (this design).
- [#3458](https://github.com/dimagi/open-chat-studio/issues/3458) — consumer acceptance criteria
  (the ACE verifier).
- [#3465](https://github.com/dimagi/open-chat-studio/issues/3465) — public-ID prerequisite
  (Track A); depends on landing first.
