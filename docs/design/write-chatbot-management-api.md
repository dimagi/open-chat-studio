---
status: active
---

# Write Chatbot Management API

> Companion to the [Read-only Chatbot Inspection API](read-only-chatbot-inspection-api.md).
> That design gave an external agent the *map* of a deployed bot (`GET /inspect/`);
> this one is the inverse — surgical, agent-driven edits to a Chatbot's **Working Version**,
> read back through `?version=N` on inspect.
> The decisions below (W1–W9) were stress-tested against the codebase (2026-05-31) and several
> were corrected against how OCS actually persists pipelines and publishes versions.
> Still `status: active` — this is a design, not yet implemented or shipped. ADR extraction is
> gated off until it is promoted to `stable`.

## TL;DR

The same class of external agent that *inspects* bots (ACE, the Connect Interviews verifier)
needs to **fix and build** them: correct a router keyword, swap a RAG collection, add a missing
24-hour inactivity timeout, assemble a pipeline from existing resources. The write API serves that
agent — **surgical mutations** of a Chatbot's **Working Version**, plus a whole-graph authoring path
for bulk work — never a bulk declarative desired-state apply.

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
| **Create content resources** — collections (incl. indexed/RAG), files, source material, surveys, consent forms, custom actions | **Yes** | The non-secret resources an agent could legitimately author. Indexed collections are async ([W8](#w8--indexed-collection-authoring-is-the-one-async-content-resource)). |
| **Detach channels** — `DELETE` a Channel binding from a Chatbot | **Yes** | Writes no secret; the prerequisite for archiving a live bot ([W9](#w9--archive-is-undo-my-own-draft-live-teardown-requires-detaching-channels-first)). |
| **Create provider credentials** — LLM/voice/messaging/auth/trace providers (encrypted `config` rows) | **No** | High blast radius; would accept secrets the read API never exposes. Reference-only. |
| **Create / configure channels** — bind a new Channel or edit its `extra_data` | **No** | Channel config holds messaging-provider secrets (`bot_token`, `widget_token`); accepting them breaks no-secrets-in. Detach is allowed, create is not. |

The line is clean: **the write API never accepts a secret.** Providers are picked from, not
created; channels can be detached but not created/configured.

### Relationship to the read design

This API is deliberately the inverse of `/inspect/`:

- `/inspect/` is a **denormalized inline tree** for single-pass reading by an LLM. The write API is
  **normalized and addressable** — you mutate one node, one trigger, one field, not a tree.
- `/inspect/` **digests** custom-action schemas and **omits** secrets. The write API **accepts**
  the full `api_schema` on the way in but still **never** accepts a secret.
- Both speak the same **numeric DB ids** for resource references (one carve-out: custom-action
  operation refs — [W4](#w4--references-by-numeric-db-id-one-carve-out-no-secrets-accepted)), so the
  read→write loop needs no translation.
- `/inspect/` is **not round-trippable** (ADR-0022), so the whole-graph authoring path
  ([W2](#w2--two-write-paths-over-one-set-of-primitives-node-façade--whole-graph-put)) reads from a
  separate raw-graph `GET`, not from inspect.

## Goals and non-goals

**Goals.**

- Surgical endpoints (node/edge/trigger/setting) **and** a whole-graph authoring path, both over the
  same persistence primitives, to create a Chatbot, compose its Pipeline, manage its Triggers, and
  author/attach content resources.
- An async publish action (with a go-live validity gate) so a fix can be taken live and verified.
- Channel detachment as the safe prerequisite for archiving a live bot.
- Mirror the read design's auth, team-scoping, versioning, and secrets posture exactly.

**Non-goals.**

- A bulk declarative "apply this whole bot" *desired-state* operation (rejected in favour of surgical
  edits + an explicit whole-graph replace).
- Creating or editing provider credentials, creating/configuring channels, or accepting any secret.
- A read-only *role* or any change to the permission model (unchanged from the read design).
- Writes to v1 or to `/inspect/`.
- General CRUD over every model for arbitrary UI consumers — the surface is scoped to what an
  agent needs to fix/build a bot.
- Hard deletes — archive (soft) only.

## Decisions

Each decision is independently supersedable. (Numbered `W#` to avoid collision with the read
design's `D#`.)

### W1 — Imperative mutations, not declarative desired-state apply

**Decision.** The write API is a set of **imperative** REST endpoints (edit a node, add a trigger,
patch a setting; or replace the whole graph — [W2](#w2--two-write-paths-over-one-set-of-primitives-node-façade--whole-graph-put)),
not a single **declarative** "here is the bot I want, reconcile to it" operation.

**Context.** The consumer is an agent making targeted fixes off the inspect map, not an IaC tool
reconciling desired state. Imperative edits give precise, local error feedback and avoid the diffing
machinery a desired-state apply requires. (Note this is orthogonal to surgical-vs-whole-graph: the
whole-graph `PUT` is still imperative — "make the graph be exactly this" — not a reconcile over the
whole *bot* incl. triggers, settings, and resources.)

**Consequences.** The agent issues a sequence of imperative calls; a multi-step build is several
requests. In exchange, every failure is local and legible, and there is no reconciler to reason about.

**Alternatives considered.** Declarative `PUT` of a normalized chatbot document — rejected: the
driver isn't provisioning, and round-trip apply is more machinery for worse error locality.
Whole-graph `PUT` of the pipeline (matching the builder UI's save) — folded into W2 as the
*internal* mechanism, not the API surface.

### W2 — Two write paths over one set of primitives: node façade + whole-graph PUT

**Decision.** Offer **both** pipeline write surfaces, layered over the *same* persistence
primitives:

- a **node/edge-addressable façade** (per-node, per-edge endpoints keyed by `flow_id`) for surgical
  edits, and
- a **whole-graph `PUT`** for bulk authoring, reusing the exact logic of the existing
  `pipeline_data` view (`apps/pipelines/views.py:381`).

Both delegate to `update_nodes_from_data()` (`apps/pipelines/models.py:178`) + `validate()` — the
façade does a server-side read-modify-write of the single `Pipeline.data` blob; the PUT replaces it
wholesale. Neither is a second persistence implementation. The whole-graph path also requires a
**raw-graph `GET`** in v2 (the digested `/inspect/` payload is not round-trippable — ADR-0022 — so
the client needs the real `{nodes, edges}` to PUT back).

**Context.** OCS's *only* pipeline write path today is `pipeline_data`: a whole-graph replace that
validates `FlowPipelineData`, overwrites `pipeline.data`, re-derives `Node` rows, and returns
`{data, errors: validate()}`. There is no per-node DB write anywhere. A surgical façade is the
better fit for an agent making targeted fixes (blast radius limited to the node it names, and it
pairs naturally with reading `/inspect/`), but bulk authoring is cleaner as one whole-graph call.

**Consequences.** The agent picks the path per task: PATCH one node to fix a keyword; PUT a full
graph to scaffold a bot. `flow_id` is **server-generated and returned** on node creation — the agent
never invents one; edges reference returned `flow_id`s. Cost: two entry points to keep behaviourally
consistent, both funnelled through the same primitives so they can't diverge in validation or
node-derivation semantics.

**Alternatives considered.** Façade only — rejected: bulk authoring becomes a long sequence of
calls. Whole-graph PUT only — rejected: forces the agent to round-trip the entire graph (incl.
positions and untouched nodes) for a one-field fix, risking collateral corruption, and needs a raw
read shape for every edit. JSON Patch / RFC-6902 — rejected: most machinery, worst errors.

### W3 — Writes target the Working Version; publishing is async, explicit, and gated on validity at go-live

**Decision.** Every mutation operates on the **Working Version** (`working_version__isnull=True`);
Chatbot Versions (snapshots) are immutable and never mutated. Publishing is a dedicated
`POST /api/v2/chatbots/{id}/versions/` that dispatches the existing `async_create_experiment_version`
task (`apps/experiments/tasks.py:45`) — so it returns **`202 Accepted` + a task id to poll** (the
model already tracks `create_version_task_id`). The request carries `make_default` (whether the new
snapshot becomes the Published Version that live channels serve). **When `make_default=true`, the API
gates on validity** — if `pipeline.validate()` returns errors the publish is rejected `422` and
nothing is snapshotted; snapshot-without-going-live (`make_default=false`) stays lenient.

**Context.** Snapshotting and publishing are separable in OCS (`make_default` on
`create_new_version`, `apps/experiments/models.py:854`) — creating a version does *not* automatically
make it the Published Version (except the first). The version-creation flow is a Celery task, not
synchronous. Crucially, `create_new_version` performs **no** validation — the builder UI lets a human
publish a knowingly-broken bot because a person is reading the red error markers. An autonomous agent
has no such eyeballs, and going live with a broken graph demotes a working Published Version and
points live channels at a bot that errors mid-conversation.

**Consequences.** The agent's loop is: edit Working Version → `POST /versions/` (poll the task) →
verify via `GET /inspect/?version=N`. The go-live validity gate is a **deliberate divergence from the
UI** — justified because the caller can't see warnings a human can; future maintainers must know the
API is stricter than the builder here. Snapshotting an invalid draft *without* publishing remains
allowed (e.g. checkpointing work in progress).

**Alternatives considered.** Gate always (even non-publishing snapshots) — rejected: blocks
checkpointing a WIP draft. No gate, match the UI exactly — rejected: lets an unattended agent point
live channels at a broken bot. Gate-always-and-fix-the-UI-to-match — rejected as out of scope (a UI
behaviour change beyond this API).

### W4 — References by numeric DB id (one carve-out); no secrets accepted

**Decision.** Resource references in write payloads use the **numeric DB id** that `/inspect/`
emits. Providers are **reference-only** (GET to pick from; no create/edit). No endpoint accepts an
encrypted `config`, signed URL, or other secret. **One carve-out:** a Pipeline Node references
**Custom Action Operations**, not whole Custom Actions, and stores them as composite
`"{custom_action_id}:{operation_id}"` strings (`make_model_id`, `apps/custom_actions/form_utils.py:93`)
— so those references use that composite string, not a bare numeric id. The server supplies the valid
operation-ref strings on a Custom Action's representation; the agent copies them verbatim into a
node's `custom_actions[]` (it never constructs the composite itself — same rule as router handles,
[W5](#w5-edges-handles-positions-and-flow_ids)).

**Context.** The read→write loop is tightest when both speak the same identifier; the read design
already settled on numeric ids and deferred public IDs
([read D4](read-only-chatbot-inspection-api.md#d4--no-new-public-ids-reuse-existing-identifiers)).
The agent reads an id from inspect and writes it straight back. The custom-action exception is forced
by how nodes already store the reference, and the glossary's Operation-vs-Custom-Action distinction.

**Consequences.** Zero migration; read/write symmetry. Cost: numeric ids aren't portable across
environments (acceptable — the agent operates within one environment). Provider credentials remain
creatable only through the existing UI/admin path. The agent must treat router handles and operation
refs as server-issued opaque strings, never compute them.

**Alternatives considered.** Introduce `public_id` on write-referenced models now (the trigger
[read D4](read-only-chatbot-inspection-api.md#d4--no-new-public-ids-reuse-existing-identifiers)
reserved for a write API) — rejected for now: a multi-model migration and an inspect payload change
for portability the agent doesn't yet need. Accept `{custom_action_id, operation_id}` as a structured
pair and build the composite server-side — rejected: diverges from the stored string format for no
gain.

### W5 — Edges, handles, positions, and flow_ids

**Decision.** On node creation the server assigns the `flow_id` and a default/auto-layout
`position`, and returns them. Edges are their own sub-resource. Edge identity is **deterministic
from `(source, sourceHandle, target, targetHandle)`** — no schema change, no persisted edge id.
**The server supplies each node's valid output handles**; the agent wires by copying a handle string
verbatim, never by computing it.

**Context.** A node POSTed by an agent isn't wired and has no canvas position, but the builder UI
needs both. React-flow edges in `data` use `sourceHandle`/`targetHandle` (`apps/pipelines/flow.py:25`)
and carry no durable external id. Standard nodes use `output`/`input`; routers fan out by **positional
index** (`output_0`, `output_1`, … where `output_i` ↔ `keywords[i]`, `apps/pipelines/graph.py:102`).
Exposing that positional convention to the agent would be brittle (breaks on keyword reorder) and
resolving by keyword label is ambiguous (keywords aren't guaranteed unique).

**Consequences.** Each node in the façade representation carries an explicit `output_handles` list
derived server-side from type + params, e.g. for a router
`[{"handle":"output_0","label":"schedule"},{"handle":"output_1","label":"reschedule"}]`, and for a
standard node `[{"handle":"output","label":null}]`. The agent's build sequence is POST node (get
`flow_id` + `output_handles`) → POST edge(s) copying a `handle`. Editing a router's `keywords` can
strand an edge whose handle no longer exists; that **surfaces in the validation report**
([W6](#w6--lenient-validation-save-draft--report-mirrors-the-existing-builder-save)) rather than being silently pruned, so the agent
sees and fixes it. Deterministic edge ids mean two identical edges are the same edge — fine for a DAG.

**Alternatives considered.** Agent supplies raw `sourceHandle` — rejected: couples the agent to the
positional convention. Wire by keyword label, server resolves index — rejected: ambiguous on
non-unique keywords. Client-supplied `flow_id` — rejected: collision risk. Persisted edge id —
rejected: schema change for no benefit.

### W6 — Lenient validation: save draft + report (mirrors the existing builder save)

**Decision.** A structurally-sound mutation **always persists**, even if it leaves the graph
semantically invalid (a node added before it's wired, a missing required param, a stranded edge). The
response carries `pipeline_valid` (bool) and a per-node `errors` map from `Pipeline.validate()`
(`apps/pipelines/models.py:209`) — **the exact `{data, errors: validate()}` contract `pipeline_data`
already returns.** Validity is *enforced* only at go-live publish ([W3](#w3-writes-target-the-working-version-publishing-is-async-explicit-and-gated-on-validity-at-go-live)).

**Context.** The builder UI saves half-built drafts and reports errors non-blockingly; the agent
builds incrementally the same way (add node, wire it, fill params). Rejecting every intermediate
invalid state would make multi-step builds impossible, and would needlessly diverge from the proven
save path.

**Consequences.** The agent iterates toward validity, reading `errors` after each step — the same
field-level feedback the UI surfaces, via the same code. Cost: a draft can sit invalid; the
go-live gate is the backstop.

**Alternatives considered.** Reject-if-invalid per mutation (`422`) — rejected: can't add a node
before wiring it; hostile to incremental assembly and to reusing `pipeline_data`'s behaviour.

### W7 — Uniform optimistic concurrency on pipeline writes

**Decision.** **Both** pipeline write paths (façade mutations and whole-graph PUT) require an
`If-Match: <etag>` header carrying the ETag returned by the pipeline read; a stale ETag →
`412 Precondition Failed`. Server-side, each mutation also takes a `select_for_update` lock on the
`Pipeline` row inside the existing `transaction.atomic()` block.

**Context.** Whole-graph PUT genuinely needs optimistic concurrency — the client builds a full blob
from a possibly-stale read, so a concurrent change would be silently overwritten. The façade is
inherently safer (it merges server-side against fresh state), but a uniform `If-Match` rule across
both paths is simpler for clients to reason about than "ETag here, not there," and the row lock
closes the read-modify-write interleave window regardless. Note `pipeline_data` takes **no** lock
today, so the existing builder has a latent last-write-wins race an autonomous agent is more likely to
hit.

**Consequences.** One concurrency rule for all pipeline writes; conflicts surface as `412` for the
agent to re-read and retry, and the row lock prevents interleaved corruption. Cost: the agent threads
the ETag through every pipeline call. Non-pipeline sub-resources (triggers, content, channel detach)
are independent rows and don't participate.

**Alternatives considered.** ETag only on whole-graph PUT, lock-only for the façade — rejected:
correct but a more complex client contract for little benefit. No concurrency control — rejected:
lost updates on a shared blob, worse under an autonomous caller.

### W8 — Indexed-collection authoring is the one async content resource

**Decision.** The agent may author content resources (media collections, files, source material,
surveys, consent forms, custom actions) **and** indexed (RAG) collections. Authoring an indexed
collection is the single explicitly-**async** resource: `POST /collections/` (with `is_index`,
embedding provider+model by id, chunking config) → `202`; add files → `202` + poll
`CollectionFile.status` (PENDING → IN_PROGRESS → COMPLETED/FAILED, `apps/documents/tasks.py`) until
`COMPLETED`; only then is the index wireable. Wiring an already-indexed collection to a node is
synchronous (a plain reference write).

**Context.** Indexed collections embed and chunk via Celery (`index_collection_files_task.delay`,
`apps/documents/views.py:431`), with per-file status tracked on `CollectionFile`. Media collections
and plain files are synchronous. "Build a bot" for the Connect Interviews workflow genuinely includes
standing up its RAG, and the async machinery already exists to lean on.

**Consequences.** Indexed-collection creation is a multi-step pollable sub-flow; everything else is
flat and mostly synchronous. The poll signal is the existing `CollectionFile.status`. Authoring an
index pulls in an embedding provider+model choice (by id, reference-only) and chunk config — these
are non-secret and safe to accept.

**Alternatives considered.** Wire-existing-indexes-only (a human builds the RAG, the agent only
attaches it) — rejected: the agent can't build a RAG bot end-to-end. Block until indexed — rejected:
long requests, timeout risk.

### W9 — Archive is "undo my own draft"; live teardown requires detaching channels first

**Decision.** `DELETE /api/v2/chatbots/{id}/` soft-archives (reusing `Experiment.archive()`,
`apps/experiments/models.py:911`) **only if the Chatbot has no Channels**; otherwise `409`. To fully
delete a live, channel-bound bot, the agent must first detach its channels via
`DELETE /api/v2/chatbots/{id}/channels/{channel_id}/`. No hard delete, ever. **Channel detachment** is
therefore in scope (it writes no secret); channel **creation/configuration** stays out of scope (it
needs messaging-provider `extra_data` secrets — [W4](#w4--references-by-numeric-db-id-one-carve-out-no-secrets-accepted)).

**Context.** `archive()` on the Working Version cascades to the *entire* family — it archives every
version, soft-deletes all channels, and deletes scheduled messages — with no guard. A naïve `DELETE`
would let an autonomous agent tear down a live bot and disconnect Telegram/WhatsApp in one call.
Channels are what make a bot "live", so gating on channel count is the precise safety boundary.

**Consequences.** The agent can clean up a bot it created and botched, but live teardown is a
deliberate two-stage act (detach each channel, then archive) — no accidental one-call decommission.
Channel creation remaining out of scope keeps the no-secrets-in invariant intact.

**Alternatives considered.** Full soft-archive behind a `force=true` flag — rejected: a single flag
is too easy for an autonomous caller to set; the channel-detach prerequisite is a more deliberate
gate. DELETE out of scope entirely — rejected: the agent legitimately needs to discard its own draft.
Hard delete — rejected: never, archive only.

## Endpoint surface

All under `/api/v2/`; `chatbot` naming per read-design D3. Working version unless noted.

### Chatbot lifecycle

```
POST   /api/v2/chatbots/                 create empty shell (name, settings; bare Start→End pipeline — W7-creation)
PATCH  /api/v2/chatbots/{id}/            chatbot-level settings + wiring (consent_form, surveys, voice…) by id
DELETE /api/v2/chatbots/{id}/            soft-archive — 409 unless no Channels remain (W9)
POST   /api/v2/chatbots/{id}/versions/   publish: 202 + poll create_version_task_id; body {make_default, description} (W3)
DELETE /api/v2/chatbots/{id}/channels/{channel_id}/   detach a channel (no secrets); prerequisite for archiving a live bot (W9)
```

`POST /chatbots/` creates an **empty shell** (bare Start→End pipeline); the agent composes everything,
including the LLM node and its provider, via the façade. Publishing is async and, when
`make_default=true`, **gated on `pipeline.validate()`** ([W3](#w3-writes-target-the-working-version-publishing-is-async-explicit-and-gated-on-validity-at-go-live)).

### Pipeline composition — two paths over one set of primitives (W2)

```
# whole-graph (bulk authoring)
GET    /api/v2/chatbots/{id}/pipeline/                  raw graph {nodes, edges} + ETag (W7) — NOT the inspect digest
PUT    /api/v2/chatbots/{id}/pipeline/                  replace whole graph (reuses pipeline_data logic)

# node/edge façade (surgical edits)
POST   /api/v2/chatbots/{id}/pipeline/nodes/            add node (type+params); server assigns flow_id + position + output_handles (W5)
PATCH  /api/v2/chatbots/{id}/pipeline/nodes/{flow_id}/  edit params/label (refs by numeric id; custom_actions by operation-ref string — W4)
DELETE /api/v2/chatbots/{id}/pipeline/nodes/{flow_id}/  remove node (+ its now-dangling edges)
POST   /api/v2/chatbots/{id}/pipeline/edges/            wire {source, sourceHandle, target, targetHandle} (copy handle verbatim — W5)
DELETE /api/v2/chatbots/{id}/pipeline/edges/{edge_id}/  unwire (edge_id deterministic from the 4-tuple — W5)
```

All pipeline writes require `If-Match` ([W7](#w7--uniform-optimistic-concurrency-on-pipeline-writes)) and
return `pipeline_valid` + `errors` ([W6](#w6--lenient-validation-save-draft--report-mirrors-the-existing-builder-save)). Example:

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

### Events / triggers (Chatbot Version-level — read D9; Triggers attach to the Chatbot, not the Pipeline)

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
POST/PATCH/DELETE  /api/v2/collections/        indexed (RAG) create is async → 202 + poll CollectionFile.status (W8); media is sync
POST/PATCH/DELETE  /api/v2/files/              multipart upload; indexing async → 202 + poll (W8)
POST/PATCH/DELETE  /api/v2/source-material/
POST/PATCH/DELETE  /api/v2/surveys/
POST/PATCH/DELETE  /api/v2/consent-forms/
POST/PATCH/DELETE  /api/v2/custom-actions/     accepts the full api_schema (inspect only digests it back)
GET (only)         /api/v2/providers/…         reference-only — secrets never writable (W4)
```

Indexed-collection authoring takes an embedding provider+model (by id) and chunk config, and is the
single explicitly-async resource ([W8](#w8--indexed-collection-authoring-is-the-one-async-content-resource)).

## Authentication and permissions

No new mechanism — the mirror of the read design's table.

| Concern | Mechanism | Notes |
|---|---|---|
| API key auth | `ApiKeyAuthentication` / `BearerTokenAuthentication` | unchanged |
| Team scoping | `request.team`; querysets filtered by team | unchanged |
| Model perm | `DjangoModelPermissionsWithView` | POST→`add_*`, PATCH→`change_*`, DELETE→`delete_*` |
| Read-only key safety | `UserAPIKey.read_only` + `ReadOnlyAPIKeyPermission` | **blocks all writes** — the inspect key literally cannot mutate |
| OAuth scope | `TokenHasOAuthResourceScope`, **new scope `chatbots:write`** | distinct from `chatbots:read` (inspect) and `chatbots:interact` (runtime conversation), `config/settings.py:909` (W9-scope) |

A write-capable key is therefore a *different* key from the read-only inspect key — by design. The
new `chatbots:write` scope is deliberately separate from `chatbots:interact`: a key that can converse
with a bot must not be able to silently reconfigure it, and vice versa. One scope covers all config
writes (create/edit/publish/archive/content authoring); per-operation scopes are YAGNI for now.

### Input-side multi-tenancy guard

Every inbound id (a node's `llm_provider_id`, a trigger action's pipeline reference, a collection
membership) is **validated against `request.team`** before use — the input-side twin of the inspect
collector's guard. A cross-team or crafted id resolves to nothing → `404`/validation error, never a
write that reaches another team's resource. See `docs/agents/multi_tenancy.md`.

## The agent's loop

```
GET   /api/v2/chatbots/{id}/inspect/            # read the map
POST  /api/v2/chatbots/{id}/pipeline/nodes/     # targeted edits to the Working Version (get flow_id + output_handles)
PATCH …/pipeline/nodes/{flow_id}/  (If-Match)   #   (reading pipeline_valid + errors each step)
POST  …/pipeline/edges/            (If-Match)   # wire by copying a server-issued handle
POST  …/timeout-triggers/                       # build the missing 24h timeout
GET   /api/v2/chatbots/{id}/inspect/            # confirm the draft
POST  /api/v2/chatbots/{id}/versions/           # publish: 202, make_default=true → validity-gated (W3); poll the task
GET   /api/v2/chatbots/{id}/inspect/?version=N  # verify the published snapshot
```

The read design's assertions #1–#5 become **write-acceptance checks**: the agent doesn't just
verify a bot is wired correctly, it drives it to that state and confirms.

## Open questions

1. **Default node params.** `POST /pipeline/nodes/` with a bare `type` — does the server fill node
   defaults (as `create_default` does for the seed LLM node), or require the agent to supply a full
   param set? Leaning server-fills-defaults, agent overrides; the resulting (likely invalid) draft is
   saved + reported per [W6](#w6--lenient-validation-save-draft--report-mirrors-the-existing-builder-save).
2. **EventAction param shape.** The nested `EventAction.params` is freeform per `action_type`
   (`pipeline_start` → pipeline id, `schedule_trigger` → cadence, `send_message_to_bot` → message).
   Each `action_type`'s param contract needs enumerating for the agent, the same way node params are.
3. **Rate / size limits** on content creation (file upload size, collection size) — deferred to when
   a real consumer exists, mirroring the read design's response-size stance.
4. **Whole-graph PUT vs inspect divergence.** The raw-graph `GET` ([W2](#w2--two-write-paths-over-one-set-of-primitives-node-façade--whole-graph-put))
   is a *third* read shape (alongside the minimal canonical GET and `/inspect/`). Worth confirming the
   maintenance cost is acceptable, or whether the façade alone would have sufficed in practice.

> **Resolved during the 2026-05-31 codebase grilling:** edge/handle vocabulary (now W5 —
> server-supplied `output_handles`); archive semantics (now W9 — channel-gated soft-archive);
> publish mechanics (now W3 — async, `make_default`, go-live validity gate); concurrency (now W7 —
> uniform `If-Match`); content-authoring scope (now W8 — indexed collections included, async).

## Related

- [Read-only Chatbot Inspection API](read-only-chatbot-inspection-api.md) — the companion read design.
- [#3452](https://github.com/dimagi/open-chat-studio/issues/3452) — parent read-inspection issue;
  this write API is its "read now, write later" continuation.
- [#3458](https://github.com/dimagi/open-chat-studio/issues/3458) — the ACE consumer whose assertions
  become write-acceptance checks here.
