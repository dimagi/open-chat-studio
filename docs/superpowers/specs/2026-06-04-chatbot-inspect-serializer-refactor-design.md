---
status: active
---

# Chatbot Inspect API — serializer-centric refactor

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

## Goals

- Eliminate the walker / collector / builder stack and all the intermediate value
  trees. Resolution and rendering happen **inside serializers**.
- Top-level and structural serializers are `ModelSerializer`s with explicit `fields`
  allowlists (never `__all__`, never a denylist — ADR-0027 stays in force).
- Composite/derived fields (e.g. `llm` = provider + model) are resolved and rendered
  by the serializer itself, via `SerializerMethodField` building on the existing
  flattening serializers.
- Preserve the security and correctness invariants: team-scoped resource access
  (ADR-0028), secrets exclusion (ADR-0027), the denormalized read-only projection
  (ADR-0024), and inlined nested resource trees (ADR-0025).
- Leave clean seams for a future **write** API (see Forward-compatibility).

## Non-goals

- Building the write API. Out of scope; we only avoid blocking it.
- Changing the OpenAPI component structure or endpoint URL/auth.
- Query-count optimisation beyond a lightweight per-request memoised fetch (see §5).

## Key decisions

| # | Decision |
|---|----------|
| 1 | Serializer-centric: the view hands the raw resolved `Experiment` to `ChatbotInspectSerializer`; everything is resolved/rendered in serializers. No walker / collector / builder / `InspectContext`. |
| 2 | Resources are fetched lazily inside serializer fields (per the "purest serializers" preference), through a per-request **memoised** `ResourceFetcher` in serializer context so a shared resource isn't re-queried. Not a collector: it never pre-walks or batch-collects ids. |
| 3 | Node resource fields are declared **explicitly** on the node serializer (no UI-signal auto-discovery walker). A hand-maintained `RESOURCE_FIELDS` map drives both which param keys are consumed and how each is rendered. |
| 4 | A **lighter completeness guard** test asserts that `RESOURCE_FIELDS` covers every resource-bearing node UI-signal currently defined, so a new unclassified field breaks CI rather than silently landing in `params`. |
| 5 | Output is **byte-for-byte identical to today, with one deliberate change**: a node renders every resource field its node *type* declares, using `null` (single) / `[]` (list) for unset/empty values instead of omitting the key. Fields the node type does **not** declare remain absent (so `StartNode`/`EndNode` still carry no resource keys). |
| 6 | Composite fields use `SerializerMethodField` + the existing flattening serializers. `ProviderModelPair`, `VoicePair`, `CustomActionSelection` survive as thin internal helpers built *inside* the method fields. |

## Architecture

### Request flow

```python
# views.py — inspect action
family = self.get_object()
target = resolve_inspect_version(family, request.query_params.get("version"))
return Response(ChatbotInspectSerializer(target, context={"team": target.team}).data)
```

`resolve_inspect_version` and `InspectVersionError` are pure version-resolution
helpers (not collector/builder) — relocated from `builder.py` to a small
`versioning.py`.

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
└── llm / voice / source_material / assistant / custom_actions
    / media_collection / indexed_collections    SerializerMethodField each

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
`InspectSettingsSerializer`.

### The composite-field pattern

A composite reads its parts off the model and renders them through the existing
flattening serializer — the "one field that depends on two fields" pattern:

```python
class ChatbotInspectSerializer(serializers.ModelSerializer):
    voice = serializers.SerializerMethodField()

    @extend_schema_field(FlattenedVoiceSerializer(allow_null=True))
    def get_voice(self, exp):
        pair = VoicePair.from_parts(exp.voice_provider, exp.synthetic_voice)
        return FlattenedVoiceSerializer(pair).data if pair else None
```

Node-level composites resolve their parts through the context fetcher:

```python
class InspectNodeSerializer(serializers.ModelSerializer):
    llm = serializers.SerializerMethodField()

    @extend_schema_field(FlattenedLlmSerializer(allow_null=True))
    def get_llm(self, node):
        fetch = self.context["fetcher"]
        pair = ProviderModelPair.from_parts(
            fetch.llm_provider(node.params.get("llm_provider_id")),
            fetch.llm_provider_model(node.params.get("llm_provider_model_id")),
        )
        return FlattenedLlmSerializer(pair).data if pair else None
```

### Node field declaration & omission rules

`RESOURCE_FIELDS` (in `resources.py`) is the hand-maintained map from a node-class
field name to its rendering, e.g.:

```python
# node_field_name -> (payload_key, ResourceKind, is_list)
RESOURCE_FIELDS = {
    "llm_provider_id":   ("llm", ResourceKind.LLM_PROVIDER_MODEL, False),  # pair
    "source_material_id":("source_material", ResourceKind.SOURCE_MATERIAL, False),
    "assistant_id":      ("assistant", ResourceKind.ASSISTANT, False),
    "custom_actions":    ("custom_actions", ResourceKind.CUSTOM_ACTION, True),
    "collection_id":     ("media_collection", ResourceKind.COLLECTION, False),
    "collection_index_id":("indexed_collections", ResourceKind.COLLECTION, True),
    # voice widget field -> ("voice", ResourceKind.SYNTHETIC_VOICE, False)
    ...  # exact field names derived from the current node pydantic classes
}
```

`InspectNodeSerializer.to_representation` keeps a resource key **iff the node's
pydantic class declares the source field** (`field_name in node_class.model_fields`),
otherwise the key is popped:

```python
def to_representation(self, node):
    data = super().to_representation(node)
    node_class = node_class_for(node.type)
    declared = node_class.model_fields if node_class else {}
    for field_name, (payload_key, _kind, _is_list) in RESOURCE_FIELDS.items():
        if field_name not in declared:
            data.pop(payload_key, None)
    return data
```

For a **declared** field the value is rendered as-is — `null` for an unset single
ref, `[]` for an empty list ref. This is the one intentional output change (decision
#5): `LLMResponseWithPrompt` nodes now always carry all of
`llm / voice / source_material / custom_actions / media_collection / indexed_collections`,
with `null`/`[]` where unset. `params` is the stored params minus the consumed
resource field names minus the redundant `name` key.

### Fetching — memoised, team-scoped (decision #2)

`ResourceFetcher(team)` lives in `resources.py` and is placed in serializer context
once by the root serializer (or the view). Each accessor is a team-scoped
`.filter(...).first()` (or `.filter(... id__in=...)` for lists), memoised per
instance so a provider/model/collection shared across nodes is queried at most once.
Team-scoping rules carry over verbatim from the current collector:

- `SourceMaterial`, `OpenAiAssistant`, `CustomAction` (`select_related("auth_provider")`),
  `Collection` (`select_related("llm_provider", "embedding_provider_model")
  .prefetch_related("files")`), `LlmProvider`, `VoiceProvider` — `team=team`.
- `LlmProviderModel` — `Q(team=team) | Q(team__isnull=True)` (global rows allowed).
- `SyntheticVoice` — global catalogue, by id (`select_related("voice_provider")`).
- Ids originate in untrusted node-param JSON, so `_as_int` coercion drops malformed
  ids (a bad id resolves to absent, never crashes) — moved into `resources.py`.

This is **not** the collector: there is no pre-walk and no `kind → ids` accumulation.
Each field fetches what it needs; memoisation only prevents firing the *identical*
query twice. (If we ever want truly fetch-per-field with no shared object, drop the
memoisation — output is unaffected.)

### Channels & events logic relocation

- The channel-collection logic (working-version channels + the read-only,
  `get_or_create`-avoiding team-global web/API channel lookup) moves verbatim into
  `get_channels` (helper in `resources.py`).
- `events.py`'s behaviour moves into the trigger/action serializers: archived-trigger
  exclusion in the parent `get_*`; `delay → delay_seconds` rename via `source=`;
  `schedule_trigger` cadence projection and `pipeline_start` pipeline-id stripping +
  team-scoped pipeline embedding (reusing `InspectPipelineSerializer`) in
  `InspectTriggerActionSerializer`.
- Node render order (Start first, End last, otherwise creation order) and the graph
  topology digest move into `InspectPipelineSerializer`'s `get_nodes`/`get_graph`.

## File layout

| File | Change |
|------|--------|
| `apps/api/v2/inspect/serializers.py` | All serializers (root, settings, leaves, flattened, node, pipeline, graph, events, triggers, action). |
| `apps/api/v2/inspect/resources.py` | **New.** `ResourceFetcher`, `RESOURCE_FIELDS`, `ResourceKind`, `_as_int`, node render-order, channel-collection helper, `node_class_for`. |
| `apps/api/v2/inspect/versioning.py` | **New.** `resolve_inspect_version`, `InspectVersionError` (relocated from `builder.py`). |
| `apps/api/v2/inspect/builder.py` | **Deleted.** |
| `apps/api/v2/inspect/collector.py` | **Deleted.** |
| `apps/api/v2/inspect/node_walker.py` | **Deleted.** |
| `apps/api/v2/inspect/events.py` | **Deleted.** |
| `apps/api/v2/views.py` | `inspect` action updated; imports from `serializers` + `versioning`. |

## Testing

**Stay green (the contract / spec):**

- `apps/api/v2/tests/test_chatbots_inspect.py` — acceptance + full body. **Updated**
  only where decision #5 applies: declared-but-unset node resource keys now appear as
  `null`/`[]` (notably `test_full_response_body` and any per-node assertions).
- `apps/api/v2/tests/test_inspect_schema.py` — unchanged; component names and
  `@extend_schema_field` annotations are preserved so the schema stays derivable.
- `apps/api/v2/inspect/tests/test_secrets_exclusion.py` — unchanged (serializers stay).
- `apps/api/v2/inspect/tests/test_flattened_serializers.py` — unchanged (flattening
  serializers and their pair inputs are retained).

**Replaced** (they test internals being deleted):

- `test_builder.py`, `test_collector.py`, `test_node_walker.py`, `test_events.py` →
  new serializer-level tests covering the same behaviours (composite resolution,
  team-scoping/cross-team absence, malformed id tolerance, memoised single-query
  fetch, pipeline-start embedding, cadence, archived exclusion, render order).
- `test_completeness_guard.py` → lighter guard: introspect every node UI-signal and
  assert `RESOURCE_FIELDS` covers each resource-bearing one (decision #4).
- `test_response.py` → rewritten to feed raw models (not a hand-built context tree);
  the absent-ref test now asserts `null`/`[]` for declared fields (decision #5).

## Forward-compatibility with a future write API (non-binding)

Not built now; the read design only avoids blocking it. The eventual write API takes
a *parallel-but-normalised* shape (same structure, ids instead of embedded objects)
and owns its own `create()`/`update()` that decomposes composites. Seams we preserve:

1. **Leaf serializers are reusable.** `ConsentFormSerializer`, `SurveySerializer`,
   `ChannelSerializer`, `InspectSettingsSerializer`, etc. are plain `ModelSerializer`s
   over real model fields with no inspect-specific coupling — a write serializer can
   reuse them directly.
2. **Projection logic is isolated.** The read-only, denormalising parts (composite
   `SerializerMethodField`s, embedded resource trees, schema digest) are confined to
   method fields, not entangled with the reusable leaves. A write serializer supplies
   its own id-bearing input fields and decomposition in `create()`/`update()`.
3. **Parallel naming.** Read field names map predictably to write inputs
   (`llm` ↔ `llm_provider_id` + `llm_provider_model_id`; `source_material` object with
   `id` ↔ `source_material_id`), so the two shapes stay recognisably aligned.

## Risks

- **N+1 query growth.** Accepted (purest-serializers preference); the memoised fetcher
  blunts the worst case (shared resources). Inspect is a low-traffic admin endpoint.
  No acceptance test asserts query counts, so output stays byte-identical regardless.
- **Lost auto-discovery.** Dropping the signal walker means a new resource-bearing node
  field must be added to `RESOURCE_FIELDS` by hand; the lighter guard test (decision #4)
  fails CI if it isn't.
- **Schema drift.** Method fields must keep `@extend_schema_field` annotations or the
  OpenAPI schema loses the field's type; `test_inspect_schema.py` guards this.
