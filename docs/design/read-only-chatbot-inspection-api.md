---
status: active
---

# Read-only Chatbot Inspection API

> Single canonical design document for the read-only chatbot-inspection API
> tracked in [#3452](https://github.com/dimagi/open-chat-studio/issues/3452),
> with consumer acceptance criteria from [#3458](https://github.com/dimagi/open-chat-studio/issues/3458).
> (A public-ID migration, [#3465](https://github.com/dimagi/open-chat-studio/issues/3465), was
> originally a prerequisite but has been dropped — see [D4](#d4-no-new-public-ids-reuse-existing-identifiers).)
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
4. **No new public IDs.** Inline nesting (below) removed the need to dereference resources by a
   stable key, so we don't add `public_id` to resource models — the chatbot keeps its existing UUID,
   nested resources carry their numeric DB id. This drops the #3465 prerequisite entirely.

The endpoint itself — `GET /api/v2/chatbots/{id}/inspect/` — returns a **denormalized,
read-only projection**: a digested pipeline graph, per-node detail, an experiment-level events
block, and an inline, denormalized tree where each node and event embeds the resources it references
(provider + model pairs grouped under a concept key), with all credentials excluded.

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
against this primarily via [D5](#d5-inspect-is-a-denormalized-read-only-projection-on-a-distinct-url)
(a separate `/inspect/` URL signalling "projection, not representation"); see also
[D4](#d4-no-new-public-ids-reuse-existing-identifiers) on why stable external IDs are deferred to
when a write API actually exists.

## Goals and non-goals

**Goals.**

- A read-only endpoint that returns a single chatbot's full configuration: identity + version,
  all non-secret settings, a digested pipeline graph, per-node detail, experiment-level events,
  and details of every resource any node or event references (LLM providers/models, collections,
  files, source material, assistants, custom actions, voices, surveys, consent forms, trigger
  actions).
- Satisfy assertions #1–#5 from [#3458](https://github.com/dimagi/open-chat-studio/issues/3458) as
  acceptance criteria.
- Establish v2 naming and URL-path versioning so the future write API is not boxed in.

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
| 3 | An LLM node points at the expected collection (RAG), and the collection has the expected files | node's embedded `indexed_collections[].files[]` (RAG) / `media_collection.files[]` |
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
existing callers if done in place. The dual-version cost is only justified if `/api/experiments/`
has real external consumers — this was **verified** (API request logs checked 2026-05-29 confirm
real external usage), so an in-place rename is off the table.

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
`/api/v2/chatbots/{id}/sessions/`. v1 keeps `experiment` naming, frozen. v2 also fixes other
misleading field names while it can — notably a `Collection`'s embedding provider, stored internally
as `llm_provider`, is surfaced as `embedding` in the payload (see [D6](#d6-inline-nested-resource-tree-denormalized)).

**Context.** The rename is already half-done and inconsistent across the API surface; v2 is a clean
break that finishes it without disturbing existing callers.

**Consequences.** The external vocabulary becomes consistent and matches the product's user-facing
"Chatbot" term. Internal model names (`Experiment`) are unaffected — this is an API-surface rename
only. v1 and v2 payloads diverge in field names, which is the intended cost of a versioned break.

**Alternatives considered.** Rename in place (no v2) — rejected because it breaks every existing
caller. Leave the inconsistency — rejected because API names ossify once consumed.

### D4 — No new public IDs; reuse existing identifiers

**Decision.** Do **not** add `public_id` fields to the resource models the v2 API exposes. The
chatbot keeps its existing `Experiment.public_id` (UUID, already the lookup field); embedded
resources carry their numeric DB primary key as `id`. This eliminates the public-ID prerequisite —
[#3465](https://github.com/dimagi/open-chat-studio/issues/3465) is no longer needed for this design.

**Context.** The original plan added a `public_id` UUID to ~21 models so that nested *references*
could be dereferenced by a stable, environment-portable key. The switch to an inline nested tree
([D6](#d6-inline-nested-resource-tree-denormalized)) removed that need entirely — the consumer never
addresses a resource by ID for reads; the resource is embedded right where it's used. What remained
was speculative write-API forward-compat and a mild "don't leak sequential PKs" concern — not enough
to justify a 21-model migration as a hard blocker on the endpoint.

**Consequences.** Inspect ships without waiting on a large migration; no `PublicIdMixin`, no
backfills, no per-version UUID reset in `create_new_version`. Cost: the read payload exposes numeric
DB primary keys for nested resources (an information-leak the team judged acceptable for a
team-scoped, read-only, authenticated endpoint), and numeric IDs are not portable across
environments. If/when a write API is specced, it can introduce stable external IDs **scoped to the
specific resources it accepts as references** — a targeted addition driven by real need rather than
a speculative sweep. The chatbot's own identifier stays the existing UUID, so the top-level handle is
already stable.

**Alternatives considered.** Add `public_id` to all v2-exposed models up front (the original D4) —
rejected: the inline tree removed the dereferencing rationale, leaving only speculative
forward-compat (YAGNI). Scope public IDs to write-addressable resources only — rejected: still a
sizable migration for a write API that has no spec yet, and it creates a mix of id types. Defer the
decision — folded into this one: the decision *is* to defer, and to revisit when a write API exists.

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
(e.g. a node carries `media_collection` / `indexed_collections` objects directly; each carries its
`files[]`). The response is a self-contained tree read top-to-bottom with no pointer-chasing. Each
embedded resource carries its numeric DB id as `id` (no new public IDs — [D4](#d4-no-new-public-ids-reuse-existing-identifiers)),
so a consumer that wants to deduplicate can build its own map keyed by `id`.

A **provider + model pair is grouped under a single concept key**: a node carries `llm = { provider,
model }` rather than sibling `llm_provider` / `llm_provider_model`, and the same grouping applies to
voice (`voice = { provider, voice }`) and an indexed collection's embedding
(`embedding = { provider, model }` — media collections have no embedding).
Provider and model are independent catalog rows (joined by `type`, not a FK), so they stay as
distinct sub-objects — grouped for readability, not merged into one (which would imply an ownership
that doesn't exist and diverge from the likely write shape of two separate references).

**Context.** The primary consumer is an LLM-agent verifier reasoning over the JSON. Locality —
having a node's resources right there beside it — matters more for that consumer than wire-size
minimisation. A resource referenced by many nodes is duplicated, but the same resource is byte-for-
byte identical at every site (same `id`), so duplication is recoverable, not lossy.

**Consequences.** Single-pass readability; a node is self-describing. Wiring is implicit in
containment (see [D10](#d10-wiring-is-implicit-in-nesting)). Cost: a shared resource is repeated at
each reference site, so payload size is non-deterministic and grows with fan-out — this amplifies the
size concern noted in [resolved Q9](#11-resolved-questions) (full payload in v1, no `?include=`
filter). Diffing two bots is harder than with a normalised table. The collector must still batch-load
each resource type once to avoid N+1, then inline copies (see [implementation](#track-b-the-inspect-endpoint-depends-on-track-a)).

**Alternatives considered.**

- **Flat lookup tables keyed by `public_id`** (nodes carry references, clients dereference) —
  deterministic dedup, predictable size, easy diffing, but the consumer must resolve every reference
  and the payload is less readable in isolation. Rejected in favour of consumer locality.
- **JSON:API compound document** (`{data, included}` with typed relationships) — standardised and
  deduped, but verbose envelope and `{type, id}` linkage boilerplate for what is a read-only
  projection, and the consumer still resolves `included[]`. Rejected.

### D7 — Signal-driven node walker with a completeness guard

**Decision.** The serializer does not hand-maintain a node-type → reference-field table. It looks up
each node's pydantic class from the registry (`apps/pipelines/nodes/__init__.py`), iterates its
model fields, and classifies each field's *UI signal* against an explicit **signal → resource-type
registry**. Fields whose signal maps to a resource type are resolved and **embedded inline** under a
named key (per [D6](#d6-inline-nested-resource-tree-denormalized)); everything else goes verbatim
into `params`.

The signal is **not** simply "has an `options_source`" — that is neither necessary nor sufficient
(see Context). The registry maps:

- a curated subset of `OptionsSource` values — `source_material`, `assistant`, `custom_actions`,
  `collection`, `collection_index`, `voice_provider_id`, `synthetic_voice_id` — to their resource
  types; **and**
- the `Widgets.llm_provider_model` widget to the `llm_provider_id` + `llm_provider_model_id` pair
  (these carry no `options_source`).

Tool fields (`agent_tools`, `built_in_tools`, `mcp_tools`) are **not** resources — they reference
enum values or external tool ids, not OCS resource models — and stay in `params`.

**Context.** `options_source` is a UI hint for populating select widgets, used for both resource
references *and* non-resources (`agent_tools`, `built_in_tools`, `mcp_tools`, `jinja_node`,
`text_editor_autocomplete_vars_*`). Worse, the most common reference — the LLM provider/model
(`apps/pipelines/nodes/mixins.py:72-73`) — carries **no** `options_source` at all; it is signalled
by the `llm_provider_model` widget. So an "any `options_source` ⇒ reference" rule would both miss
the LLM provider/model on every LLM/Router/Boolean/Extract node *and* wrongly embed tool enums.

**Consequences.** Adding a node type that reuses existing field signals requires no serializer
change. **Introducing a new kind of resource reference does** require registering its signal — and a
**completeness-guard test** makes that loud, not silent: the test enumerates every `OptionsSource`
value and every `Widget` and asserts each is classified as either "embeds resource X" or "explicitly
not a resource", failing CI when a new, unclassified signal appears. This converts the
silent-omission risk (the failure mode [#3458](https://github.com/dimagi/open-chat-studio/issues/3458)
exists to prevent) into a build break.

**Alternatives considered.** *"Any field with an `options_source` is a reference"* — rejected: it
misses the LLM provider/model (no `options_source`) and wrongly includes tool enums. A static
node-type → field mapping table — rejected as a maintenance liability that drifts silently; the
signal registry + completeness guard achieves the same coverage without per-node-type upkeep.

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

**Decision.** The payload includes a top-level `events` block (peer to `pipeline`) with
`static_triggers[]` and `timeout_triggers[]`, each nesting its `EventAction` (`action_type` +
`params`). This is in addition to the node walk.

**Context.** `StaticTrigger` and `TimeoutTrigger` (`apps/events/models.py`) attach to the
experiment, not the pipeline graph. A node-only walk silently omits them — including the 24-hour
inactivity timeout that is assertion #4 and the single most important thing the Connect Interviews
verifier checks.

**Consequences.** Assertion #4 becomes reachable. `EventLog` entries are excluded (operational
telemetry, not config). `EventAction.params` reference other resources, embedded inline on the action
(per [D6](#d6-inline-nested-resource-tree-denormalized)): `pipeline_start` embeds the referenced
pipeline with the **same `{ id, name, graph, nodes:[...] }` shape as the top-level pipeline** — graph
digest *and* fully-detailed nodes with their own inline resources ([resolved Q2](#11-resolved-questions)).
This is self-contained and does not recurse: a pipeline has no triggers of its own (triggers attach
to chatbots, not pipelines), so embedding a referenced pipeline pulls in its nodes/resources but no
further events. `schedule_trigger` embeds the `ScheduledMessage` cadence
([resolved Q3](#11-resolved-questions)). Disabled triggers are included but flagged via `is_active`,
so a verifier can assert a trigger *isn't* armed.

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

**Transitive exposure — a deliberate choice.** inspect authorizes solely on
`experiments.view_experiment` + team scope. It intentionally does **not** enforce per-resource
`view_*` permissions on the embedded resources (collections, files, custom actions, providers,
assistants, scheduled messages) — so a key with only `view_experiment` can read all of a chatbot's
wiring across apps. This is acceptable because every embedded resource is **team-scoped** and already
co-visible to anyone who can view the chatbot (there is no intra-team gating that lets someone view a
chatbot but not its collections/actions/providers); the consumer is a team-scoped `read_only` key.
Flagged explicitly so it reads as a conscious decision, not an accident, in security review.

## Response shape

Inline nested tree ([D6](#d6-inline-nested-resource-tree-denormalized)). Each node and event embeds
the resources it references. The chatbot's own `id` is its existing UUID; embedded resources carry
their numeric DB `id` (no new public IDs — [D4](#d4-no-new-public-ids-reuse-existing-identifiers)).
Provider + model pairs are grouped under a concept key. Credentials are excluded ([D8](#d8-secrets-exclusion-via-per-resource-serializers-with-explicit-field-lists)).

A **Pipeline** has a single canonical serialized shape — `{ id, name, version_number, graph, nodes:[…] }`
(`graph` = topology, `nodes` = per-node detail with resources embedded) — used **identically**
wherever a pipeline appears: at the top level, and embedded under a `pipeline_start` event action. One
shape, one parser.

```jsonc
{
  "chatbot": {
    "id": "5a3c…",                        // Experiment.public_id (existing UUID)
    "name": "Customer Support Bot",
    "description": "…",
    "version_number": 0,
    "is_working_version": true,
    "is_default_version": false,
    "version_description": null,
    "team_slug": "acme",
    "settings": {
      // non-secret Experiment fields, null if unset. NOTE: prompt/temperature/tools/
      // source_material/citations live on the LLM node now, NOT on the chatbot — the
      // legacy Experiment-level fields (temperature, tools, citations_enabled, prompt_text,
      // input_formatter, source_material) are removed/not surfaced.
      "seed_message": null,
      "conversational_consent_enabled": false,
      "voice_response_behaviour": "reciprocal",
      "echo_transcript": false,
      "participant_allowlist": []
    },

    // chatbot-level resources embedded inline (numeric db id; null if unset)
    "consent_form":    { "id": 3, "name": "Default", "consent_text": "…", "capture_identifier": true },
    "pre_survey":      null,
    "post_survey":     { "id": 9, "name": "CSAT", "url": "https://…" },
    "voice": {                            // provider + voice grouped (D6)
      "provider": { "id": 4, "type": "elevenlabs", "name": "ElevenLabs Prod" },
      "voice":    { "id": 12, "name": "Rachel", "language": "English" }
    },
    "trace_provider":  null,
    "safety_layers":   [],

    "channels": [                         // ExperimentChannel — secrets stripped (resolved Q8)
      { "platform": "telegram", "name": "Support TG",
        "messaging_provider": { "id": 6, "type": "telegram", "name": "Support TG bot" } }
    ]
  },

  // canonical Pipeline object — identical shape wherever a pipeline appears
  // (top level here, and embedded under a pipeline_start event action)
  "pipeline": {
    "id": 42,
    "name": "Support flow v3",
    "version_number": 0,
    "graph": {                            // topology — positions stripped, edges kept (trim of Pipeline.data_without_positions)
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
    },
    "nodes": [                            // detail — one entry per graph node, resources embedded inline
      {
        "flow_id": "llm-1",
        "type": "LLMResponseWithPrompt",
        "label": "Classify intent",
        "params": {                       // non-reference fields, verbatim
          "prompt": "You are…",
          "history_type": "global",
          "tools": []
        },
        // reference fields resolved + embedded inline via the signal registry (D7); null if unset
        "llm": {                          // provider + model grouped (D6)
          "provider": { "id": 5, "type": "openai", "name": "Prod OpenAI" },
          "model":    { "id": 18, "type": "openai", "name": "gpt-4o", "max_token_limit": 128000, "deprecated": false }
        },
        "source_material": { "id": 7, "topic": "Returns policy", "material": "# Returns\n…" },
        "media_collection": {             // node field collection_id ("Media") — files, NO embedding provider
          "id": 21, "name": "Policy docs",
          "files": [                      // collection files embedded → assertion #3
            { "id": 101, "name": "returns.pdf", "content_type": "application/pdf", "content_size": 50321, "purpose": "collection" }
          ]
        },
        "indexed_collections": [          // node field collection_index_ids (list) — RAG indexes, WITH embedding
          {
            "id": 33, "name": "Policy index",
            "embedding": {                // embedding provider+model grouped (D6); from the collection's llm_provider (D3 rename)
              "provider": { "id": 5, "type": "openai", "name": "Prod OpenAI" },
              "model":    { "id": 7, "type": "openai", "model_name": "text-embedding-3-small" }
            },
            "files": [
              { "id": 201, "name": "policy.pdf", "content_type": "application/pdf", "content_size": 40112, "purpose": "collection" }
            ]
          }
        ],
        "custom_actions": [               // wired-to-this-node by containment → assertion #5 (D10)
          { "id": 12, "name": "Session Completion", "server_url": "https://…",
            "allowed_operations": ["complete_session"],
            "api_schema": { "paths": ["/complete_session"] },    // path/operation digest only (resolved Q7)
            "auth_provider": { "id": 2, "type": "oauth", "name": "Partner API auth" } }  // name/type only; config excluded (D8)
        ]
      }
      // … one entry per node
    ]
  },

  "events": {                             // experiment-level — D9
    "static_triggers": [
      {
        "id": 11,
        "type": "conversation_end",       // StaticTriggerType
        "is_active": true,
        "action": {
          "id": 47, "action_type": "pipeline_start",
          // pipeline_start embeds the referenced pipeline using the SAME canonical Pipeline
          // object as the top level (resolved Q2). Self-contained; no recursion (a pipeline
          // has no triggers of its own — triggers attach to chatbots, not pipelines).
          "pipeline": {
            "id": 99, "name": "Completion flow", "version_number": 0,
            "graph": { "nodes": [ /* flow_id/type/label */ ], "edges": [ /* … */ ] },
            "nodes": [ /* detailed nodes with embedded resources — same as pipeline.nodes above */ ]
          }
        }
      }
    ],
    "timeout_triggers": [
      {
        "id": 22,
        "delay_seconds": 86400,           // 24h inactivity → assertion #4; v2 rename of model field `delay` (D3)
        "total_num_triggers": 1,
        "trigger_from_first_message": false,
        "is_active": true,
        "action": {
          "id": 48, "action_type": "send_message_to_bot",
          "params": { "message": "Are you still there?" }
          // schedule_trigger actions instead embed: "scheduled_message": { … cadence … } (resolved Q3)
        }
      }
    ]
  }
}
```

A resource referenced from more than one site (e.g. one `LlmProvider` used by several nodes) appears
inline at each site, byte-for-byte identical and sharing the same numeric `id`. Consumers that need
to deduplicate index by `id`.

## Node type → reference field mapping

The walker is signal-driven ([D7](#d7-signal-driven-node-walker-with-a-completeness-guard)); this
table is illustrative, not hand-maintained in code. Derived from `apps/pipelines/nodes/nodes.py` and
`apps/pipelines/nodes/mixins.py`. These are the *source* fields the walker reads; in the payload a
`llm_provider_id` + `llm_provider_model_id` pair renders grouped under `llm = { provider, model }`
([D6](#d6-inline-nested-resource-tree-denormalized)). Note `llm_provider_id`/`llm_provider_model_id`
are matched by the `llm_provider_model` *widget* (no `options_source`); the rest by their
`options_source` value. Payload renames (D3): `collection_id` → `media_collection` (files, no
embedding), `collection_index_ids` → `indexed_collections` (RAG, with embedding). Tool fields
(`agent_tools`, `built_in_tools`, `mcp_tools`) are not resources and stay in `params` (D7).

| Node class | Reference fields |
|---|---|
| `LLMResponse` | `llm_provider_id`, `llm_provider_model_id` |
| `LLMResponseWithPrompt` | above + `source_material_id`, `collection_id` (→ `media_collection`), `collection_index_ids[]` (→ `indexed_collections`), `custom_actions[]`, `synthetic_voice_id` |
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
| `Collection` | `apps/documents/models.py` | — (`openai_vector_store_id` **exposed** — opaque pointer, not a credential; see [resolved Q6](#11-resolved-questions)). Rendered two ways: `media_collection` (files, no embedding) and `indexed_collections` (RAG: embedding provider+model + files). |
| `File` | `apps/files/models.py` | **`file` URL** — signed storage URL; never expose. Also **`summary` + `metadata` omitted** from the inline file object — not secrets, but dropped for size (duplicated under inline nesting; not needed for assertion #3). Embedded file is identity-lean: `id, name, content_type, content_size, external_source, external_id, purpose` (Q6). |
| `OpenAiAssistant` | `apps/assistants/models.py` | — (`instructions` and `assistant_id` **exposed**; see [resolved Q4/Q5](#11-resolved-questions)) |
| `CustomAction` | `apps/custom_actions/models.py` | **full `api_schema`** — reduce to path/operation digest (OpenAPI docs can embed `securitySchemes` with key examples). Embeds its `auth_provider` as `{id, type, name}` only (config excluded); `server_url` exposed (team-set config). |
| `ExperimentChannel` | `apps/channels/models.py` | **entire `extra_data`** — freeform auth material (`bot_token`, `widget_token`, …); allowlist `platform` + `name` only (resolved Q8) |

The audit lives in code as a resource-type → serializer registry, never `__all__`.

## Versioning resolution

- `?version=<n>` → resolve the matching `Experiment` version
  (`working_version=root, version_number=n`); `?version=default` → `is_default_version=True`.
- **Snapshotted vs live resources.** Creating a version snapshots the pipeline, its nodes, and the
  node-referenced `source_material` / `collection` / `collection_index` / `assistant` and
  custom-action operations (versioning repoints the ids in node params —
  `apps/pipelines/models.py:413-417`), plus the experiment-level `source_material`/`consent_form`/
  surveys and triggers. These reflect the state at publish time. **Not** snapshotted are the
  non-versioned shared rows — `llm_provider` / `llm_provider_model`, `voice_provider`,
  `synthetic_voice`: a node's `llm_provider_id` is *not* repointed, so for a published version these
  still reflect the **current** (live) config. This is acceptable because providers are exposed as
  name/type only (no secrets), so liveness is harmless; we deliberately add **no payload signal**
  distinguishing snapshotted from live resources.
- Either way the walker needs no special logic: versioning already repointed the snapshotted ids, so
  it just reads each node's ids and loads those rows.
- The response always includes `is_working_version`, `version_number`, `is_default_version` so the
  client knows what it received.
- For working-version requests, triggers come from `experiment.static_triggers.all()` /
  `experiment.timeout_triggers.all()`; for versioned requests, from their own `working_version` FKs.

## Implementation plan

Ordered so each step ships independently and stays reviewable. Two logical tracks, each its own set
of PRs. (The earlier public-ID prerequisite track and #3465 are dropped per
[D4](#d4-no-new-public-ids-reuse-existing-identifiers).)

### Track A — v2 routing + frozen v1

1. Enable `URLPathVersioning`; `ALLOWED_VERSIONS = ["v1", "v2"]`. Restructure `apps/api/urls.py`
   under `path("api/<str:version>/", …)`. Wire existing routes under `/api/v1/` and keep
   `/api/experiments/…` as a permanent alias.
2. v2 chatbot + session ViewSets: rename surface (`chatbots`, `chatbot_id`, `chatbot_*` operation
   IDs, `"Chatbots"` tag), nest sessions under chatbots. Per-version OpenAPI schemas + Swagger UIs.

### Track B — the `/inspect/` endpoint (depends on Track A)

3. Add the `inspect` action to the v2 chatbot ViewSet (`extend_schema(operation_id="chatbot_inspect")`).
4. Inspect serializers in a new module (e.g. `apps/api/serializers_inspect.py`): top-level
   `ChatbotInspectSerializer` + per-resource `ModelSerializer`s with explicit `fields`. Provider +
   model pairs render under a grouped concept key (`llm`, `voice`, `embedding`) per
   [D6](#d6-inline-nested-resource-tree-denormalized).
5. Pipeline node walker (e.g. `apps/pipelines/inspect.py`): walk `pipeline.node_set.all()`, look up
   each pydantic class via the registry, classify fields by the D7 signal registry. Non-reference fields →
   `params`; for each reference field record `(field_name, resource_type, id)` and accumulate
   `resource_refs: dict[ResourceKey, set[id]]` so the collector can pre-load in batch.
6. Events serializer (e.g. `apps/events/api_serializers.py`): `StaticTrigger` + `TimeoutTrigger`
   with nested `EventAction`; record event-referenced resources (`pipeline_start` → pipeline,
   `schedule_trigger` → scheduled message) the same way. Channels are read from `experiment.channels`
   and their `messaging_provider` added to `resource_refs`.
7. Resource collector + inliner: batch-load each resource type once
   (`Model.objects.filter(id__in=…)` with `select_related`/`prefetch_related` — **one query per
   type, no N+1**, regardless of how many sites reference it), serialize through the secret-safe
   serializers into an `id`-keyed map, then **inline** the serialized object at each reference site
   under its named key ([D6](#d6-inline-nested-resource-tree-denormalized)). Duplication across
   sites is by design. Covers providers, collections/files, source material, consent/surveys,
   assistants, custom actions, messaging providers, event-referenced pipelines, and scheduled
   messages. (Batch-loading is the N+1 guard; inlining copies from the in-memory map, not the DB.)

   **Team-scope every batch-load (multi-tenancy guard).** The ids come from node `params` JSON and
   must not be trusted — every load is scoped to `request.team` so a stray/crafted cross-team id
   resolves to nothing rather than leaking another team's resource (see `docs/agents/multi_tenancy.md`).
   Scoping differs by model, in three categories:
   - **Direct `team` FK** (`BaseTeamModel`): `LlmProvider`, `LlmProviderModel`*, `VoiceProvider`,
     `MessagingProvider`, `AuthProvider`, `EmbeddingProviderModel`*, `SourceMaterial`, `ConsentForm`,
     `Survey`, `Collection`, `File`, `OpenAiAssistant`, `CustomAction`, `Pipeline` → filter on `team`.
     (* `LlmProviderModel`/`EmbeddingProviderModel` allow global rows with null team — include
     `Q(team=request.team) | Q(team__isnull=True)`.)
   - **Scoped via experiment** (not team-scoped directly): `StaticTrigger`, `TimeoutTrigger`,
     `EventAction`, `ScheduledMessage`, `ExperimentChannel` → constrain to the chatbot being
     inspected, not loaded by bare id.
   - **Global, no scoping** : `SyntheticVoice` (`BaseModel`, not team-scoped) — safe to load by id.
8. Tests (`apps/api/tests/test_chatbots_inspect.py`) — see below.
9. OpenAPI schema regeneration + verification; docs page describing payload shape and the
   secrets-exclusion policy.

### Test plan

- **Auth:** anonymous → 401; wrong team → 404; `read_only` API key → 200.
- **Versioning:** working version by default; `?version=N` returns the right snapshot;
  `?version=default` resolves the default published version.
- **Node coverage:** a pipeline exercising each node type verifies the signal-registry split.
- **Completeness guard (D7):** a test enumerating every `OptionsSource` value and `Widget` fails when
  a new, unclassified signal appears — preventing silent omission of a new resource reference.
- **Cross-team leak:** craft a node whose `params` references another team's resource id; assert the
  collector's team-scoping resolves it to absent (not embedded) rather than leaking it.
- **Secret-leak:** for each provider, assert `config` (and every other excluded key) appears
  nowhere in the response JSON (`assertNotIn` against the serialized payload).
- **Acceptance #1–#5** (from [#3458](https://github.com/dimagi/open-chat-studio/issues/3458)):
  identity; router keywords; node's embedded `indexed_collections[].files[]` inventory; 24-hr `TimeoutTrigger` +
  its embedded action in the `events` block; custom action embedded under the node/event that fires it.
- **Inline shape:** a resource referenced by two nodes appears (identically) under both; assert the
  duplicated objects share the same `id`.

## 11. Resolved questions

All resolved 2026-05-29. Recorded here so the rationale survives into ADR extraction.

1. **Permission granularity** → **reuse the existing `view` permission.** No dedicated
   `inspect_chatbot` perm; a `read_only` key with `view` access can inspect. A least-privilege
   inspect-only perm can be added later if a concrete need appears.
2. **`EventAction.params` for `pipeline_start`** → **embed the referenced pipeline, full shape.** The
   `pipeline_start` action embeds the referenced pipeline inline using the same
   `{ id, name, graph, nodes:[...] }` structure as the top-level pipeline (graph digest *and*
   detailed nodes with their embedded resources), so a verifier can inspect trigger-launched flows
   without a second request. Self-contained and non-recursive — a pipeline carries no triggers of its
   own, so no further events are pulled in.
3. **`schedule_trigger` / `ScheduledMessage`** → **include the cadence.** The `schedule_trigger`
   action embeds the `ScheduledMessage` cadence inline.
4. **`OpenAiAssistant.instructions`** → **expose as-is.** Prompt text the verifier may want to
   assert; accepted residual risk that a team could embed a secret in a prompt.
5. **`OpenAiAssistant.assistant_id`** → **expose.** Opaque OpenAI-side ID, not a credential.
6. **`Collection.openai_vector_store_id`** → **expose.** Treated as an opaque pointer rather than a
   credential. (Note: this went *against* the initial lean to exclude — recorded deliberately.)
7. **`CustomAction.api_schema`** → **path/operation digest.** Strip to operation IDs, paths, and
   summaries; never expose the raw schema (it can embed `securitySchemes` with key examples).
8. **Channels** → **include, explicit allowlist (not a denylist).** Surface `ExperimentChannel`
   under `chatbot.channels[]` with an explicit allowlist of `platform` + `name` + the embedded
   `messaging_provider`. **`extra_data` is not exposed at all** — it is a freeform JSONField holding
   per-platform authorization material (`bot_token`, `widget_token`, …), so a "strip the secrets"
   denylist would violate [D8](#d8-secrets-exclusion-via-per-resource-serializers-with-explicit-field-lists)
   (opt-in exposure, never denylist). The deployment-audit use case only needs which platforms the
   bot is exposed on, which `platform` + `name` answers. A specific non-secret `extra_data` field can
   be added to a per-platform allowlist later if a real need appears.
9. **Response size** → **ship the full payload in v1.** No `?include=` selective-expansion filter
   for now; revisit only if a real consumer hits size problems.

### A later decision: dropping public IDs

Originally these resolutions (Q2, Q3, Q8) added models to a public-ID migration prerequisite
(#3465). That prerequisite was **dropped entirely** when the public-ID decision was reversed — see
[D4](#d4-no-new-public-ids-reuse-existing-identifiers). Embedded resources now carry their numeric DB
`id`, so no model needs a new `public_id` and #3465 is no longer part of this work.

## Related issues

- [#3452](https://github.com/dimagi/open-chat-studio/issues/3452) — parent: read-only bot
  inspection API (this design).
- [#3458](https://github.com/dimagi/open-chat-studio/issues/3458) — consumer acceptance criteria
  (the ACE verifier).
- [#3465](https://github.com/dimagi/open-chat-studio/issues/3465) — public-ID migration, **no longer
  a prerequisite** for this design (see [D4](#d4-no-new-public-ids-reuse-existing-identifiers)). Can
  be closed or repurposed for a future write API if/when one is specced.
