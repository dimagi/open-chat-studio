---
status: active
---

# Chatbot Inspect API — serializer-centric refactor

> **Revision note (2026-06-04 review).** This doc was revised after a structured plan
> review. The original proposal was a *full* serializer-centric rewrite with a lazy,
> memoised per-field fetcher and a thin `field_name → tuple` resource map. The review
> kept the serializer-centric *rendering* goal but reversed the riskiest trades: the
> batch loader stays (N+1-free), resource resolution is driven by a richer
> payload-keyed map, and the new invariants are pinned by tests. The "Key decisions"
> table below is the current contract; see the inline rationale for what changed.

## Background

The v2 chatbot inspect endpoint (`GET /api/v2/chatbots/{id}/inspect/`) returns a
denormalized, read-only projection of a chatbot's full configuration: identity,
`settings`, `consent_form`, surveys, `voice`, `trace_provider`, `channels`, the
`pipeline` (graph digest + per-node detail with inlined resources), and `events`
(static/timeout triggers). The exact response shape is the contract — see
`apps/api/v2/tests/test_chatbots_inspect.py::test_full_response_body`.

### Current architecture (being replaced)

A four-stage pipeline assembles the payload *before* serialization:

1. **Walkers** (`node_walker.py`, `events.py`) — introspect each pipeline node's
   pydantic field UI-signals (`options_source` / `widget`) to discover which params
   are resource references, emit typed `Ref` value objects (`LlmRef`, `VoiceRef`,
   `SingleRef`, `ListRef`, `CustomActionsRef`) plus a `kind → ids` batch-load map.
2. **Collector** (`collector.py`) — batch-loads each resource kind once (team-scoped,
   no N+1), resolves refs into value objects (`ProviderModelPair`, `VoicePair`,
   `CustomActionSelection`).
3. **Builder** (`builder.py`) — assembles a pre-chewed `InspectContext` dict tree.
4. **Serializers** (`serializers.py`) — render the already-resolved tree; they do
   little real work.

### Problem

The walker → collector → builder indirection produces a zoo of intermediate
representations (`NodeWalkResult`, `PipelineWalk`, `EventsWalk`, the `Ref` union,
`InspectContext`) that exist only to feed thin serializers. The serializers don't
own resolution — they render dicts someone else pre-built. We want the **serializers
to own resolution and rendering**: hand a serializer the raw model, let it resolve
its own fields (including composites) and render them.

**What we keep, after review.** Two properties of the current design are valuable and
cheap to retain, so we do *not* throw them away:

- **N+1-free batch loading.** The collector's one-query-per-kind loading survives,
  reshaped as a `ResourceFetcher` placed in serializer context (see §"Fetching").
  Serializers own *rendering*; the fetcher owns *loading*.
- **Drift protection.** The walker's signal-classification knowledge survives in the
  completeness-guard test, which now also asserts the hand-maintained resource map
  covers every resource-bearing node field (decision #4).

## Goals

- Eliminate the **builder / `InspectContext`** intermediate tree and the `Ref` value
  zoo. Resolution-into-render happens **inside serializers**, reading pre-batched
  resources from context.
- Top-level and structural serializers are `ModelSerializer`s with explicit `fields`
  allowlists (never `__all__`, never a denylist — ADR-0027 stays in force).
- Composite/derived fields (e.g. `llm` = provider + model) are resolved and rendered
  by the serializer itself, via `SerializerMethodField` building on the existing
  flattening serializers.
- Preserve the security and correctness invariants: team-scoped resource access
  (ADR-0028), secrets exclusion (ADR-0027), the denormalized read-only projection
  (ADR-0024), inlined nested resource trees (ADR-0025), **and the N+1-free query
  profile**.
- Leave clean seams for a future **write** API (see Forward-compatibility).

## Non-goals

- Building the write API. Out of scope; we only avoid blocking it.
- Changing the OpenAPI component structure or endpoint URL/auth.

## Key decisions

| # | Decision |
|---|----------|
| 1 | **Serializer-centric rendering with a retained batch loader (hybrid).** The view resolves + prefetches the target `Experiment`, builds a batch-loading `ResourceFetcher`, and hands both to `ChatbotInspectSerializer` via context. Serializers own *rendering* and composite resolution; a lightweight id-collection pre-pass + the fetcher own *batch loading*. `builder.py` and `InspectContext` are deleted; the batch-loading collector survives, reshaped as `ResourceFetcher`. |
| 2 | **Resources are batch-loaded once (N+1-free), not fetched lazily.** A single `iter_resource_refs(node_type, params)` traversal — driven by `RESOURCE_FIELDS`, recursing into `pipeline_start`-embedded pipelines — collects `kind → ids`; the fetcher batch-loads each kind team-scoped into by-id maps. Serializer accessors are dict lookups, never queries. A `django_assert_num_queries` test over an adversarial fixture guards the invariant. |
| 3 | **Node resource fields are declared explicitly** in a hand-maintained `RESOURCE_FIELDS` map (no UI-signal auto-discovery in production). The map is keyed by **payload key** and records the *set* of consumed node fields, the `ResourceKind`, and list-ness — so composites (`llm` = provider + model) and multi-source keys (`voice`) are modelled correctly. |
| 4 | **A two-layer completeness guard test:** (1) every node UI-signal is classified (a new signal fails CI); (2) every *resource-bearing* signal has a `RESOURCE_FIELDS` entry (a new unclassified resource field fails CI rather than silently landing in `params`). The walker's signal-classification logic survives here, as the guard's source of truth. |
| 5 | **Output is identical to today except one documented contract change:** a node renders every resource field its node *type* declares, using `null` (single) / `[]` (list) for unset/empty instead of omitting the key. Fields the node type does **not** declare stay absent (so `StartNode`/`EndNode` carry no resource keys). Called out explicitly as a contract change in the PR (not "byte-for-byte"). |
| 6 | **Composite fields use `SerializerMethodField` + the existing flattening serializers, computed only for the fields a node type declares** (no compute-then-discard). `ProviderModelPair`, `VoicePair`, `CustomActionSelection` survive as thin internal helpers built inside the method fields. Every manually-instantiated nested serializer is passed `context=self.context`. |

## Architecture

### Request flow

```python
# views.py — inspect action
family = self.get_object()
try:
    target = resolve_inspect_version(family, request.query_params.get("version"))
except InspectVersionError as err:
    raise NotFound("Requested chatbot version was not found.") from err

# Prefetch is applied to the RESOLVED target, not the family — ?version=N / =default
# return a different object, and its prefetches must not be lost (review issue #16).
target = prefetch_inspect_target(target)            # select_related FKs + prefetch nodes/triggers
fetcher = ResourceFetcher.for_experiment(target)    # id-collection pre-pass + one batch query per kind
return Response(
    ChatbotInspectSerializer(target, context={"team": target.team, "fetcher": fetcher}).data
)
```

`resolve_inspect_version`, `InspectVersionError`, and `prefetch_inspect_target` are
pure target-resolution/preparation helpers (not collector/builder) — they live in a
small `versioning.py`.

### Serializer tree

```
ChatbotInspectSerializer(ModelSerializer on Experiment)
├── id / name / description / version_number / is_unreleased / is_published_version
│   / version_description / team_slug            (model fields, renamed via source=)
├── settings           InspectSettingsSerializer(source="*")
├── consent_form       ConsentFormSerializer        (FK, ModelSerializer)
├── pre_survey         SurveySerializer             (FK)
├── post_survey        SurveySerializer             (FK)
├── trace_provider     ProviderSerializer           (FK)
├── voice              SerializerMethodField  → FlattenedVoiceSerializer   (composite)
├── channels           SerializerMethodField  → ChannelSerializer(many)    (computed)
├── pipeline           SerializerMethodField  → InspectPipelineSerializer  (nullable)
└── events             SerializerMethodField  → InspectEventsSerializer

InspectPipelineSerializer(ModelSerializer on Pipeline)
├── id / name / version_number     (model fields)
├── graph              SerializerMethodField   (topology digest)
└── nodes              SerializerMethodField  → InspectNodeSerializer(many), ordered

InspectNodeSerializer(ModelSerializer on Node)
├── node_id            CharField(source="flow_id")
├── type / label       (model fields)
├── params             SerializerMethodField   (stored params − consumed keys − "name")
└── <declared resource keys only>   built in to_representation (decision #6 / #10A):
    llm / voice / source_material / assistant / custom_actions
    / media_collection / indexed_collections — only those the node TYPE declares

InspectEventsSerializer(source="*")
├── static_triggers    InspectStaticTriggerSerializer(many)   (archived excluded)
└── timeout_triggers   InspectTimeoutTriggerSerializer(many)

InspectStaticTriggerSerializer / InspectTimeoutTriggerSerializer
  (ModelSerializer; delay → delay_seconds via source)
└── action             InspectTriggerActionSerializer

InspectTriggerActionSerializer
├── type               (action_type)
├── params             SerializerMethodField   (cadence for schedule_trigger;
│                                                pipeline_id stripped for pipeline_start)
└── pipeline           SerializerMethodField  → InspectPipelineSerializer (pipeline_start only)
```

The **rendering** serializers carry over essentially unchanged: `FlattenedProviderSerializer`,
`FlattenedLlmSerializer`, `FlattenedEmbeddingSerializer`, `FlattenedVoiceSerializer`,
`MediaCollectionSerializer`, `IndexedCollectionSerializer`, `CustomActionSerializer`,
`ApiSchemaDigestSerializer`, `ProviderSerializer`, `ConsentFormSerializer`,
`SurveySerializer`, `SourceMaterialSerializer`, `AssistantSerializer`, `FileSerializer`,
`ChannelSerializer`, `GraphNodeSerializer`, `GraphEdgeSerializer`, `GraphSerializer`,
`InspectSettingsSerializer`. These leaf/flattening serializers are **context-free**
(they take a model or a pair, never read `self.context`) — that keeps them reusable by
a future write API and keeps the fetcher dependency confined to the inspect serializers.

### Context contract (review issue #6, #8)

DRF auto-propagates `context` only to **declared** fields, **not** to serializers you
instantiate inside a `SerializerMethodField`. Therefore:

- **Every manually-instantiated nested serializer is passed `context=self.context`.**
  This is mandatory for `InspectTriggerActionSerializer → InspectPipelineSerializer`
  (pipeline embedding), whose nodes need the `fetcher` to resolve their resources.
- Inspect serializers that need the fetcher read `self.context["fetcher"]` and **fail
  loud** with a clear message if it is missing (an inspect serializer rendered without
  a fetcher is a programming error, not a runtime condition to paper over).
- Leaf/flattening serializers must stay context-free (see above).

### The composite-field pattern

A composite reads its parts off the model / fetcher and renders them through the
existing flattening serializer — the "one field that depends on two fields" pattern.
Experiment-level composites read FK columns directly (prefetched on the target):

```python
class ChatbotInspectSerializer(serializers.ModelSerializer):
    voice = serializers.SerializerMethodField()

    @extend_schema_field(FlattenedVoiceSerializer(allow_null=True))
    def get_voice(self, exp):
        pair = VoicePair.from_parts(exp.voice_provider, exp.synthetic_voice)
        return FlattenedVoiceSerializer(pair).data if pair else None   # leaf: context-free
```

Node-level composites resolve their parts through the context fetcher (dict lookups,
not queries):

```python
class InspectNodeSerializer(serializers.ModelSerializer):
    def _render_llm(self, node):
        fetch = self.context["fetcher"]
        pair = ProviderModelPair.from_parts(
            fetch.llm_provider(node.params.get("llm_provider_id")),
            fetch.llm_provider_model(node.params.get("llm_provider_model_id")),
        )
        return FlattenedLlmSerializer(pair).data if pair else None
```

### Node field declaration & rendering (decisions #3, #5, #6; review issues #9, #10)

`RESOURCE_FIELDS` (in `resources.py`) is the hand-maintained, **payload-keyed** map.
Each entry records the *set* of node fields it consumes, its `ResourceKind`, and
list-ness — rich enough to model composites and multi-source keys:

```python
@dataclasses.dataclass(frozen=True)
class ResourceField:
    consumes: frozenset[str]   # node-param field name(s) this payload key consumes
    kind: ResourceKind
    is_list: bool

# payload_key -> ResourceField   (exact field names derived from the node pydantic classes)
RESOURCE_FIELDS = {
    # composite: consumes BOTH mixin fields, so neither leaks into ``params``
    "llm":                 ResourceField({"llm_provider_id", "llm_provider_model_id"},
                                         ResourceKind.LLM_PROVIDER_MODEL, is_list=False),
    # multi-source: declared if EITHER source field is present on the node type
    "voice":               ResourceField({"voice_provider_id", "synthetic_voice_id"},
                                         ResourceKind.SYNTHETIC_VOICE, is_list=False),
    "source_material":     ResourceField({"source_material_id"},  ResourceKind.SOURCE_MATERIAL, False),
    "assistant":           ResourceField({"assistant_id"},        ResourceKind.ASSISTANT, False),
    "custom_actions":      ResourceField({"custom_actions"},      ResourceKind.CUSTOM_ACTION, True),
    "media_collection":    ResourceField({"collection_id"},       ResourceKind.COLLECTION, False),
    "indexed_collections": ResourceField({"collection_index_id"}, ResourceKind.COLLECTION, True),
}

def declared_resource_keys(node_class) -> list[str]:
    """Payload keys whose source field(s) the node type declares (any-of intersection).
    Models the multi-source ``voice`` case: declared iff ANY consumed field is present."""
    fields = node_class.model_fields.keys() if node_class else set()
    return [key for key, rf in RESOURCE_FIELDS.items() if rf.consumes & fields]
```

`InspectNodeSerializer` builds **only the declared keys** — no render-then-pop, so a
`StartNode` never computes resource fields it would discard, and the multi-source
`voice` key is never dropped because one of its two source fields is absent:

```python
def to_representation(self, node):
    data = super().to_representation(node)          # node_id / type / label / params
    node_class = node_class_for(node.type)
    for key in declared_resource_keys(node_class):
        data[key] = self._render_resource(key, node)   # null (single) / [] (list) when unset
    return data
```

`params` is the stored params minus the union of consumed fields for declared keys
minus the redundant `name`:

```python
def get_params(self, node):
    consumed = {f for key in declared_resource_keys(node_class_for(node.type))
                for f in RESOURCE_FIELDS[key].consumes}
    return {k: v for k, v in node.params.items() if k not in consumed and k != "name"}
```

This is the one intentional output change (decision #5): `LLMResponseWithPrompt` nodes
now always carry all of `llm / voice / source_material / custom_actions /
media_collection / indexed_collections`, with `null`/`[]` where unset. It is
**documented as a contract change in the PR**.

### Fetching — batch-loaded, team-scoped, N+1-free (decisions #1, #2; review issues #5, #7)

`ResourceFetcher` lives in `resources.py` and is built once from the resolved target
by the view, then placed in serializer context. It is the collector reshaped: it
batch-loads, it does not lazily query per field.

```
ResourceFetcher.for_experiment(target):
  1. id-collection pre-pass — iter_resource_refs() over every pipeline node AND every
     pipeline_start-embedded pipeline's nodes, yielding (kind, raw_id); _as_int drops
     malformed ids. Accumulates {kind: set[int]}.
  2. one batch query per kind (team-scoped) into by-id dicts.
  3. accessors — llm_provider(id) / llm_provider_model(id) / source_material(id) / ...
     — are pure dict lookups; an id not loaded (cross-team, malformed, absent) → None.
```

**Single shared traversal (review issue #5).** `iter_resource_refs(node_type, params)`
is the *one* definition of "which params are resources, by kind" — used by the
pre-pass. The render pass uses the same `RESOURCE_FIELDS` lookups per node. Both must
visit the same graph, **including `pipeline_start`-embedded pipelines**; if they
diverge, a resource the render pass embeds wasn't pre-loaded → silent miss. The
`django_assert_num_queries` test (§Testing) pins this with a fixture that includes an
embedded trigger pipeline.

Team-scoping rules carry over verbatim from the current collector:

- `SourceMaterial`, `OpenAiAssistant`, `CustomAction` (`select_related("auth_provider")`),
  `Collection` (`select_related("llm_provider", "embedding_provider_model")
  .prefetch_related("files")`), `LlmProvider`, `VoiceProvider` — `team=team`.
- `LlmProviderModel` — `Q(team=team) | Q(team__isnull=True)` (global rows allowed).
- `SyntheticVoice` — global catalogue, by id (`select_related("voice_provider")`).
- Ids originate in untrusted node-param JSON, so `_as_int` coercion drops malformed
  ids (a bad id resolves to absent, never crashes).

**Top-level fan-out (review issue #7).** `prefetch_inspect_target` applies
`select_related` for the experiment's own FKs (`consent_form`, `pre_survey`,
`post_survey`, `trace_provider`, `voice_provider`, `synthetic_voice`) and
`prefetch_related` for the pipeline's nodes and the trigger sets — applied to the
**resolved** target (issue #16), so versioned reads keep the same query profile.

### Channels & events logic relocation

- The channel-collection logic (working-version channels + the read-only,
  `get_or_create`-avoiding team-global web/API channel lookup) moves into a
  `get_channels` helper (in `channels.py`), called from the root serializer's
  `get_channels` method field.
- `events.py`'s behaviour moves into the trigger/action serializers: archived-trigger
  exclusion in the parent `get_*`; `delay → delay_seconds` rename via `source=`;
  `schedule_trigger` cadence projection and `pipeline_start` pipeline-id stripping +
  team-scoped pipeline embedding (reusing `InspectPipelineSerializer`, passed
  `context=self.context`) in `InspectTriggerActionSerializer`.
- Node render order (Start first, End last, otherwise creation order) and the graph
  topology digest move into `InspectPipelineSerializer`'s `get_nodes`/`get_graph`,
  built on pure helpers (`node_render_order`, `graph_digest`) that are unit-testable
  without a DB.

## File layout

A light split along the resolution / presentation-registry / channels seam (review
issue #11) — not one grab-bag module, not a heavy fan-out.

| File | Change |
|------|--------|
| `apps/api/v2/inspect/serializers.py` | All serializers (root, settings, leaves, flattened, node, pipeline, graph, events, triggers, action). |
| `apps/api/v2/inspect/resources.py` | **New.** Resolution concern: `ResourceFetcher` (batch loader), `RESOURCE_FIELDS`, `ResourceField`, `ResourceKind`, `_as_int`, `iter_resource_refs`. |
| `apps/api/v2/inspect/nodes.py` | **New.** Presentation/registry: `node_class_for`, `node_render_order`, `declared_resource_keys`, `graph_digest`. |
| `apps/api/v2/inspect/channels.py` | **New.** Channel-collection helper. |
| `apps/api/v2/inspect/versioning.py` | **New.** `resolve_inspect_version`, `InspectVersionError`, `prefetch_inspect_target` (relocated/added). |
| `apps/api/v2/inspect/builder.py` | **Deleted.** |
| `apps/api/v2/inspect/collector.py` | **Deleted** — batch-loading logic reshaped into `resources.ResourceFetcher`. |
| `apps/api/v2/inspect/node_walker.py` | **Deleted** — signal-classification logic relocated to the completeness-guard test. |
| `apps/api/v2/inspect/events.py` | **Deleted** — logic moved into the trigger/action serializers. |
| `apps/api/v2/views.py` | `inspect` action updated (resolve → prefetch → build fetcher → serialize); imports from `serializers` + `versioning`. |

## Testing

Test strategy follows the "separate pure logic from DB access" preference (review
issue #14): the map-driven logic and traversal are unit-tested on plain data without
`@pytest.mark.django_db`; only the fetcher's querysets, channel collection, and
end-to-end shape need the DB.

**Stay green (the contract / spec):**

- `apps/api/v2/tests/test_chatbots_inspect.py` — acceptance + full body. **Updated**
  only where decision #5 applies: declared-but-unset node resource keys now appear as
  `null`/`[]` (notably `test_full_response_body` and per-node assertions).
- `apps/api/v2/tests/test_inspect_schema.py` — unchanged; component names and
  `@extend_schema_field` annotations are preserved so the schema stays derivable.
- `apps/api/v2/inspect/tests/test_secrets_exclusion.py` — unchanged (serializers stay).
- `apps/api/v2/inspect/tests/test_flattened_serializers.py` — unchanged (flattening
  serializers and their pair inputs are retained).

**New pure-unit tests (no `@pytest.mark.django_db`):**

- `iter_resource_refs(node_type, params)` over plain `(type, params)` data — yields the
  right `(kind, id)` set per node type; recurses into embedded-pipeline structure.
- `declared_resource_keys` / consumed-field derivation — given a node class (or a stub
  exposing `model_fields`), the right keys are declared and the right fields consumed.
- `node_render_order`, `graph_digest`, `_as_int`, `ProviderModelPair.from_parts`,
  `VoicePair.from_parts` — pure.
- Map-driven node rendering with a **dict-backed fetcher stub** (no DB): composite
  resolution, `null`/`[]` for declared-but-unset.

**Regression unit tests for the map-shape bugs (review issue #15):**

- `llm_provider_model_id` is **not** present in `params` (composite consumes both fields).
- `voice` renders correctly when only **one** of its two source fields is declared/set
  (multi-source key is not dropped).

**DB tests (`@pytest.mark.django_db` + factories):**

- `ResourceFetcher` team-scoping: cross-team ids resolve to absent; global
  `LlmProviderModel` (`team__isnull=True`) and global `SyntheticVoice` rows load;
  `select_related`/`prefetch_related` shapes hold.
- Channel-collection helper; archived-trigger exclusion.
- End-to-end: full body incl. decision-#5 `null`/`[]`; cadence; render order;
  **`pipeline_start` trigger embedding a resource-bearing pipeline** (the context-
  propagation e2e test, review issue #6).

**Query-count guard (review issues #5, #12, #16):**

- `django_assert_num_queries` over an **adversarial fixture** — a multi-node pipeline,
  a `pipeline_start` trigger embedding a *second* resource-bearing pipeline, and a
  resource shared across two nodes (proves batch dedup). **Parametrized across version
  modes** (working / `default` / specific number) so prefetch-on-resolved-target is
  verified on every path.

**Completeness guard — two layers (decision #4, review issue #13):**

- Layer 1: every node UI-signal (`OptionsSource` / `Widgets`) is classified as
  resource-bearing or not (a new signal fails CI). The classification sets relocated
  from `node_walker.py` are the source of truth.
- Layer 2: every *resource-bearing* signal has a corresponding `RESOURCE_FIELDS` entry
  (a new resource field with no entry fails CI rather than silently landing in
  `params`). Asserted in the direction `{resource-bearing signals} ⊆ {covered}`.

**Replaced** (they test internals being deleted): `test_builder.py`,
`test_collector.py`, `test_node_walker.py`, `test_events.py`, `test_response.py` → the
unit/DB/e2e tests above.

## Forward-compatibility with a future write API (non-binding)

Not built now; the read design only avoids blocking it. The eventual write API takes a
*parallel-but-normalised* shape (same structure, ids instead of embedded objects) and
owns its own `create()`/`update()` that decomposes composites. Seams we preserve:

1. **Leaf serializers are reusable and context-free.** `ConsentFormSerializer`,
   `SurveySerializer`, `ChannelSerializer`, `InspectSettingsSerializer`, etc. are plain
   `ModelSerializer`s over real model fields with no inspect-specific coupling and no
   `self.context` reads — a write serializer can reuse them directly.
2. **Projection logic is isolated.** The read-only, denormalising parts (composite
   `SerializerMethodField`s, embedded resource trees, schema digest) are confined to
   method fields, not entangled with the reusable leaves. The `ResourceFetcher`
   dependency is read-specific and lives only in the inspect serializers.
3. **Parallel naming.** Read field names map predictably to write inputs
   (`llm` ↔ `llm_provider_id` + `llm_provider_model_id`; `source_material` object with
   `id` ↔ `source_material_id`), so the two shapes stay recognisably aligned. The
   `RESOURCE_FIELDS.consumes` sets already name the write-side id fields.

## Risks

- **Pre-pass / render divergence.** The id-collection pre-pass and the serializer
  render pass are two traversals that must agree (which fields are resources, which
  nodes incl. embedded trigger pipelines). Mitigated by a single `RESOURCE_FIELDS`
  source of truth + the adversarial query-count test (decision #2, issue #5/#12).
- **Lost auto-discovery.** A new resource-bearing node field must be added to
  `RESOURCE_FIELDS` by hand; the two-layer completeness guard (decision #4) fails CI
  if it isn't.
- **Map-shape correctness.** Composites (`llm`) and multi-source keys (`voice`) must be
  modelled by the payload-keyed `consumes` sets, or fields leak into `params` / get
  dropped. Pinned by the regression unit tests (issue #15).
- **Contract change (decision #5).** Declared-but-unset node resource keys now render
  `null`/`[]` instead of being omitted. Documented in the PR; consumers are internal.
- **Prefetch on the wrong object.** Prefetch must target the resolved version, not the
  family, or versioned reads regress to N+1. Applied in `prefetch_inspect_target` and
  verified by the version-parametrized query-count test (issue #16).
- **Schema drift.** Method fields must keep `@extend_schema_field` annotations or the
  OpenAPI schema loses the field's type; `test_inspect_schema.py` guards this.
