---
status: active
---

# Write Chatbot Management API

> Companion to the [Read-only Chatbot Inspection API](read-only-chatbot-inspection-api.md).
> That design gave an external agent the *map* of a deployed bot (`GET /inspect/`);
> this one is the inverse — surgical, agent-driven edits to a bot's **working draft**,
> read back through `?version=N` on inspect.
> `status: active` — this is a sketch, not yet stress-tested against the codebase. ADR
> extraction is gated off until it is promoted to `stable`.

## TL;DR

The same class of external agent that *inspects* bots (ACE, the Connect Interviews verifier)
needs to **fix and build** them: correct a router keyword, swap a RAG collection, add a missing
24-hour inactivity timeout, assemble a pipeline from existing resources. The write API serves that
agent — **fine-grained, surgical mutations** of a chatbot's *working draft*, never a bulk
declarative apply.

It lands entirely in **v2** ([read-design D2/D3](read-only-chatbot-inspection-api.md#d2--url-path-api-versioning-v1-frozen--v2-new)),
reuses the existing auth/team-scoping/permission machinery, and is the mirror image of the read
design's guarantees:

- **`/inspect/` stays a read-only projection** ([read D5](read-only-chatbot-inspection-api.md#d5--inspect-is-a-denormalized-read-only-projection-on-a-distinct-url)) —
  writes never land on it. They land on the canonical resource and its sub-resources.
- **No secret is ever accepted**, the input-side twin of the read design's never-emit
  ([read D8](read-only-chatbot-inspection-api.md#d8--secrets-exclusion-via-per-resource-serializers-with-explicit-field-lists)).
  Providers are reference-only.
- **References use the numeric DB ids `/inspect/` already emits** — no new public IDs, no
  migration ([read D4](read-only-chatbot-inspection-api.md#d4--no-new-public-ids-reuse-existing-identifiers)).

## Context

### The use case

The read API ([#3452](https://github.com/dimagi/open-chat-studio/issues/3452)) was driven by a
concrete consumer that reads a deployed bot and asserts it is wired correctly. The natural next
step for that agent is to *act on* what it finds: a verifier that can only report "the timeout is
missing" is half a tool; one that can add the timeout closes the loop.

The driver is therefore an **LLM agent making targeted fixes and assembling bots** — not an
operator running an idempotent infrastructure-as-code apply, and not a general management UI
needing CRUD over every model. That framing chooses fine-grained mutation over bulk
document-apply, and it chooses a *surgical* shape (edit this node, add that trigger) over
round-tripping the whole `/inspect/` tree.

### What the agent may mutate

Three concentric bands, decided up front:

| Band | In scope? | Notes |
|---|---|---|
| **Chatbot composition** — pipeline graph + node params, the events/triggers block, chatbot-level settings, the wiring itself | **Yes** | The core of fix/build. |
| **Wire to existing resources** — point nodes/events at existing collections, custom actions, assistants, providers, voices by reference | **Yes** | Attach-only; no creation. |
| **Create content resources** — collections, files, source material, surveys, consent forms, custom actions | **Yes** | The non-secret resources an agent could legitimately author. |
| **Create provider credentials** — LLM/voice/messaging/auth/trace providers (encrypted `config` rows) | **No** | High blast radius; would accept secrets the read API never exposes. Reference-only. |

The line is clean: **the write API never accepts a secret.** Providers are picked from, not
created.

### Relationship to the read design

This API is deliberately the inverse of `/inspect/`:

- `/inspect/` is a **denormalized inline tree** for single-pass reading by an LLM. The write API is
  **normalized and addressable** — you mutate one node, one trigger, one field, not a tree.
- `/inspect/` **digests** custom-action schemas and **omits** secrets. The write API **accepts**
  the full `api_schema` on the way in but still **never** accepts a secret.
- Both speak the same **numeric DB ids** for resource references, so the read→write loop needs no
  translation.

## Goals and non-goals

**Goals.**

- Fine-grained endpoints to create a chatbot, edit its settings, compose its pipeline
  (nodes + edges), manage its triggers, and author/attach content resources.
- A publish action so a fix can be taken live.
- Mirror the read design's auth, team-scoping, versioning, and secrets posture exactly.

**Non-goals.**

- A bulk declarative "apply this whole bot" operation (rejected in favour of surgical edits).
- Creating or editing provider credentials, or accepting any secret.
- A read-only *role* or any change to the permission model (unchanged from the read design).
- Writes to v1 or to `/inspect/`.
- General CRUD over every model for arbitrary UI consumers — the surface is scoped to what an
  agent needs to fix/build a bot.

## Decisions

Each decision is independently supersedable. (Numbered `W#` to avoid collision with the read
design's `D#`.)

### W1 — Fine-grained surgical mutations, not bulk apply

**Decision.** The write API is a set of fine-grained REST endpoints (edit a node, add a trigger,
patch a setting), not a single declarative "apply this chatbot document" operation.

**Context.** The consumer is an agent making targeted fixes off the inspect map, not an IaC tool
reconciling desired state. Surgical edits give precise, local error feedback and avoid the diffing
machinery a whole-document apply requires.

**Consequences.** Many small endpoints; the agent issues a sequence of calls. A multi-step build is
several requests, not one. In exchange, every failure is local and legible.

**Alternatives considered.** Declarative `PUT` of a normalized chatbot document — rejected: the
driver isn't provisioning, and round-trip apply is more machinery for worse error locality.
Whole-graph `PUT` of the pipeline (matching the builder UI's save) — folded into W2 as the
*internal* mechanism, not the API surface.

### W2 — Node-addressable façade over the pipeline blob

**Decision.** Expose per-node and per-edge endpoints addressed by `flow_id`. Internally the server
read-modify-writes the single `Pipeline.data` graph blob and re-derives `Node` rows via the
existing `update_nodes_from_data()` (`apps/pipelines/models.py:178`).

**Context.** `Pipeline.data` is the canonical react-flow graph (`{nodes, edges}` with positions);
`Node` rows are *derived* from it. OCS has no per-node DB write path — the builder UI saves the
whole graph. A surgical API is nonetheless the better fit for an agent, so we put a node-addressable
façade in front of the blob.

**Consequences.** The agent edits one node without resending the graph. Cost: the façade hides that
nodes live in a blob, so edges, positions, and validation need deliberate handling
([W5](#w5-edges-positions-and-flow_ids), [W6](#w6-lenient-validation-save-draft--report)).
`flow_id` is **server-generated and returned** on node creation — the agent never invents one; edges
reference returned `flow_id`s.

**Alternatives considered.** Whole-graph `PUT` as the surface — rejected: forces the agent to
resend the entire graph for a one-field fix and to manage the blob's edge/position structure
itself. JSON Patch / RFC-6902 document apply — rejected: most machinery, hardest to give precise
errors.

### W3 — Writes target the working draft; publishing is an explicit action

**Decision.** Every mutation operates on the **working version** (`working_version__isnull=True`).
Published versions are immutable snapshots and are never mutated. A dedicated
`POST /api/v2/chatbots/{id}/versions/` snapshots the working version into a new published version
(the existing `create_new_version` flow).

**Context.** Versioning already treats published versions as frozen snapshots
([read design, Versioning resolution](read-only-chatbot-inspection-api.md#versioning-resolution)).
A fix isn't "done" until it's live, so the agent needs to publish; but liveness must be an explicit
step, not a side effect of editing.

**Consequences.** The agent's loop is: edit draft → publish → verify the published snapshot via
`GET /inspect/?version=N`. Editing a non-working version is a `409`. Mutating published config is
structurally impossible.

**Alternatives considered.** Drafts-only (publishing stays a human/UI action) — rejected: leaves
the agent's fix half-applied and unverifiable end-to-end. Auto-publish on every write — rejected:
makes every keystroke-equivalent live; no safe staging.

### W4 — References by numeric DB id; no secrets accepted

**Decision.** Resource references in write payloads use the **numeric DB id** that `/inspect/`
emits. Providers are **reference-only** (GET to pick from; no create/edit). No endpoint accepts an
encrypted `config`, signed URL, or other secret.

**Context.** The read→write loop is tightest when both speak the same identifier; the read design
already settled on numeric ids and deferred public IDs
([read D4](read-only-chatbot-inspection-api.md#d4--no-new-public-ids-reuse-existing-identifiers)).
The agent reads an id from inspect and writes it straight back.

**Consequences.** Zero migration; perfect read/write symmetry. Cost: numeric ids aren't portable
across environments (acceptable — the agent operates within one environment, reading then writing).
Provider credentials remain creatable only through the existing UI/admin path.

**Alternatives considered.** Introduce `public_id` on write-referenced models now (the trigger
[read D4](read-only-chatbot-inspection-api.md#d4--no-new-public-ids-reuse-existing-identifiers)
reserved for a write API) — rejected for this sketch: a multi-model migration and an inspect payload
change, for portability the agent use case doesn't need yet. Revisit if a cross-environment
provisioning consumer appears.

### W5 — Edges, positions, and flow_ids

**Decision.** On node creation the server assigns the `flow_id` and a default/auto-layout
`position`, and returns them. Edges are their own sub-resource. Edge identity is **deterministic
from `(source, source_handle, target, target_handle)`** — no schema change, no persisted edge id.

**Context.** A node POSTed by an agent isn't wired and has no canvas position, but the builder UI
needs both. React-flow edges in `data` don't carry a durable external id, so `DELETE …/edges/{id}/`
needs a stable handle.

**Consequences.** The agent's build sequence is POST node (get `flow_id`) → POST edge(s) referencing
it. The human builder UI still renders sanely because positions are auto-assigned. Deterministic
edge ids mean two identical edges can't coexist (they're the same edge) — acceptable for a DAG.

**Alternatives considered.** Client-supplied `flow_id` — rejected: collision risk and a worse agent
ergonomics. Persisting an explicit edge id — rejected: schema change for no agent benefit; the tuple
is already unique.

### W6 — Lenient validation: save draft + report

**Decision.** A structurally-sound mutation **always persists**, even if it leaves the graph
semantically invalid (a node added before it's wired, a missing required param). The response
carries `pipeline_valid` (bool) and a per-node `errors` map from the full pydantic node validation
(`Pipeline.validate()`, `apps/pipelines/models.py:209`).

**Context.** The builder UI lets a human save a half-built draft; the agent builds incrementally the
same way (add node, then wire it, then fill params). Rejecting every intermediate invalid state
would make multi-step builds impossible.

**Consequences.** The agent iterates toward validity, reading `errors` after each step — precise,
field-level feedback (the same validation the UI surfaces). Cost: a draft can sit invalid; publishing
([W3](#w3-writes-target-the-working-draft-publishing-is-an-explicit-action)) is where validity is
*enforced* — `POST /versions/` rejects an invalid working version.

**Alternatives considered.** Reject-if-invalid per mutation (`422`, transactional against validity)
— rejected: can't add a node before wiring it; hostile to incremental assembly.

### W7 — Optimistic concurrency on the pipeline blob

**Decision.** All pipeline mutations require an `If-Match: <etag>` header carrying the ETag returned
by `GET /pipeline/`. A stale ETag → `412 Precondition Failed`.

**Context.** Every pipeline write is a read-modify-write of the single `Pipeline.data` blob. Two
in-flight mutations would silently clobber each other (lost update).

**Consequences.** Concurrent edits are caught, not lost; the agent re-reads and retries on `412`.
Cost: the agent must thread the ETag through its calls. Non-pipeline sub-resources (triggers,
content) are independent rows and don't need this.

**Alternatives considered.** Server-side row lock with no client token — rejected: hides the
conflict from the agent instead of surfacing it. No concurrency control — rejected: lost updates on
a shared blob.

### W8 — Async content creation returns pending + poll

**Decision.** Creating a RAG collection or uploading files kicks off embedding/indexing tasks. Those
endpoints return `202 Accepted` with a status the agent polls, rather than blocking the request.
Wiring an *already-indexed* collection to a node is synchronous (a plain reference write).

**Context.** Collection indexing and file embedding are Celery tasks (the read design notes media
processing is async). A synchronous create would block on a long task.

**Consequences.** Content creation has a two-step shape (create → poll until ready), reusing the
existing poll pattern from the chat endpoints (`apps/api/urls.py` chat poll routes). Wiring stays
synchronous, so the common agent action (attach an existing resource) has no extra round-trip.

**Alternatives considered.** Block until indexed — rejected: long requests, timeout risk. Keep
collection *creation* out of scope (wire-existing only) — rejected: the agent legitimately needs to
author RAG content; the poll cost is acceptable.

## Endpoint surface

All under `/api/v2/`; `chatbot` naming per read-design D3. Working version unless noted.

### Chatbot lifecycle

```
POST   /api/v2/chatbots/                 create (name, settings; seeds a default pipeline)
PATCH  /api/v2/chatbots/{id}/            chatbot-level settings + wiring (consent_form, surveys, voice…) by id
DELETE /api/v2/chatbots/{id}/            archive (soft delete)
POST   /api/v2/chatbots/{id}/versions/   publish: snapshot working → version N (create_new_version)
```

### Pipeline composition (façade over `Pipeline.data` — W2)

```
GET    /api/v2/chatbots/{id}/pipeline/                  current graph + ETag (W7)
POST   /api/v2/chatbots/{id}/pipeline/nodes/            add node (type+params); server assigns flow_id + position (W5)
PATCH  /api/v2/chatbots/{id}/pipeline/nodes/{flow_id}/  edit params/label (incl. llm_provider_id, collection_index_ids… by id)
DELETE /api/v2/chatbots/{id}/pipeline/nodes/{flow_id}/  remove node (+ its dangling edges)
POST   /api/v2/chatbots/{id}/pipeline/edges/            wire {source, source_handle, target, target_handle}
DELETE /api/v2/chatbots/{id}/pipeline/edges/{edge_id}/  unwire (edge_id deterministic from the tuple — W5)
```

All pipeline writes require `If-Match` ([W7](#w7-optimistic-concurrency-on-the-pipeline-blob)) and
return `pipeline_valid` + `errors` ([W6](#w6-lenient-validation-save-draft--report)). Example:

```jsonc
PATCH /api/v2/chatbots/{id}/pipeline/nodes/router-1/
If-Match: "a1b2c3"
{ "params": { "keywords": ["schedule", "reschedule"] } }

200 OK
ETag: "d4e5f6"
{
  "node": { "flow_id": "router-1", "type": "RouterNode", "params": { … } },
  "pipeline_valid": true,
  "errors": {}
}
```

### Events / triggers (experiment-level — read D9)

```
POST   /api/v2/chatbots/{id}/static-triggers/         add (type + nested EventAction)
PATCH  /api/v2/chatbots/{id}/static-triggers/{tid}/   edit
DELETE /api/v2/chatbots/{id}/static-triggers/{tid}/   remove
POST   /api/v2/chatbots/{id}/timeout-triggers/        add (delay_seconds, … + nested EventAction)
PATCH  /api/v2/chatbots/{id}/timeout-triggers/{tid}/  edit
DELETE /api/v2/chatbots/{id}/timeout-triggers/{tid}/  remove
```

Each carries its nested `EventAction` (`action_type` + `params`; `pipeline_start` references a
pipeline by id). This is where assertion #4's 24-hour inactivity timeout gets **built**, not just
read.

### Content resources (standalone, team-scoped)

```
POST/PATCH/DELETE  /api/v2/collections/        (+ file membership); create is async → 202 + poll (W8)
POST/PATCH/DELETE  /api/v2/files/              multipart upload; async → 202 + poll (W8)
POST/PATCH/DELETE  /api/v2/source-material/
POST/PATCH/DELETE  /api/v2/surveys/
POST/PATCH/DELETE  /api/v2/consent-forms/
POST/PATCH/DELETE  /api/v2/custom-actions/     accepts the full api_schema (inspect only digests it back)
GET (only)         /api/v2/providers/…         reference-only — secrets never writable (W4)
```

## Authentication and permissions

No new mechanism — the mirror of the read design's table.

| Concern | Mechanism | Notes |
|---|---|---|
| API key auth | `ApiKeyAuthentication` / `BearerTokenAuthentication` | unchanged |
| Team scoping | `request.team`; querysets filtered by team | unchanged |
| Model perm | `DjangoModelPermissionsWithView` | POST→`add_*`, PATCH→`change_*`, DELETE→`delete_*` |
| Read-only key safety | `UserAPIKey.read_only` + `ReadOnlyAPIKeyPermission` | **blocks all writes** — the inspect key literally cannot mutate |
| OAuth scope | `TokenHasOAuthResourceScope`, scope `chatbots` | writes need an `interact`/write-capable scope, not `read` |

A write-capable key is therefore a *different* key from the read-only inspect key — by design.

### Input-side multi-tenancy guard

Every inbound id (a node's `llm_provider_id`, a trigger action's pipeline reference, a collection
membership) is **validated against `request.team`** before use — the input-side twin of the inspect
collector's guard. A cross-team or crafted id resolves to nothing → `404`/validation error, never a
write that reaches another team's resource. See `docs/agents/multi_tenancy.md`.

## The agent's loop

```
GET  /api/v2/chatbots/{id}/inspect/            # read the map
POST /api/v2/chatbots/{id}/pipeline/nodes/     # targeted edits to the draft
PATCH …/pipeline/nodes/{flow_id}/              #   (reading pipeline_valid + errors each step)
POST …/timeout-triggers/                       # build the missing 24h timeout
GET  /api/v2/chatbots/{id}/inspect/            # confirm the draft
POST /api/v2/chatbots/{id}/versions/           # go live (rejects if invalid — W3/W6)
GET  /api/v2/chatbots/{id}/inspect/?version=N  # verify the published snapshot
```

The read design's assertions #1–#5 become **write-acceptance checks**: the agent doesn't just
verify a bot is wired correctly, it drives it to that state and confirms.

## Open questions

1. **Edge handle vocabulary.** Per-node-type `source_handle`/`target_handle` names (e.g. a router's
   per-branch handles) aren't yet enumerated for the agent. The write API likely needs to expose the
   valid handles for a node type (a small schema endpoint) so the agent can wire branches correctly.
2. **Default node params.** `POST /pipeline/nodes/` with a bare `type` — does the server fill node
   defaults (as `create_default` does for the seed LLM node), or require the agent to supply a full
   param set? Leaning server-fills-defaults, agent overrides.
3. **Archive semantics.** `DELETE /chatbots/{id}/` — soft archive vs hard delete, and whether a
   published-and-deployed bot can be archived via API at all.
4. **Rate / size limits** on content creation (file upload size, collection size) — deferred to when
   a real consumer exists, mirroring the read design's response-size stance.

## Related

- [Read-only Chatbot Inspection API](read-only-chatbot-inspection-api.md) — the companion read design.
- [#3452](https://github.com/dimagi/open-chat-studio/issues/3452) — parent read-inspection issue;
  this write API is its "read now, write later" continuation.
- [#3458](https://github.com/dimagi/open-chat-studio/issues/3458) — the ACE consumer whose assertions
  become write-acceptance checks here.
