# Inspect API — Replace ResourceFetcher with Node FK Relationships

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `apps/api/v2/inspect/serializers.py` to read the resource FK/M2M relations now present on `Node` (added in the `cs/param_fk` branch, migration `0026`) instead of resolving ids through `ResourceFetcher`. Delete `ResourceFetcher` (`apps/api/v2/inspect/resources.py`) entirely.

This is the "inspect API" follow-up listed in `docs/superpowers/plans/2026-06-15-node-resource-fks.md`.

**Decision (confirmed with the user):** *Trust the FK mirror.* The FK columns are a derived mirror of `params`, written within a team context, with DB-level referential integrity. The inspect serializers will read them directly with **no read-time team scoping**. This drops ADR-0028's read-time defense-in-depth — see "Security follow-up" below.

**Architecture:**
- `params` stays authoritative; the serializers read the FK/M2M relations that mirror it.
- **Prefer declarative DRF fields.** Now that resources are real relations, the simple references become plain nested serializer fields with `source=` and let DRF do the work: `source_material`, `assistant`, `media_collection` (←`collection`), `indexed_collections` (←`collection_indexes`, `many=True`), and the root `pipeline` (←`experiment.pipeline`). Only the genuinely composite references stay `SerializerMethodField`:
  - `llm` / `voice` — flatten two source objects into one, render `null` when both are empty, and reuse the shared `ProviderModelPair` / `VoicePair` value objects (also rendered for collection embeddings and the experiment-level voice), so they can't be a single `source=` mapping.
  - `custom_actions` — groups `custom_action_operations` by action with operation order taken from params.
  - embedded-trigger `pipeline` — a JSON `pipeline_id`, not an FK (see below).
- The **per-node-type key dropping** moves out of the `_ABSENT` sentinel and into a single `to_representation` that drops each conditional key whose backing param the node type doesn't declare (`node.has_parameter(...)`). This works uniformly for declarative *and* method fields. (`_ABSENT` stays only in `ChannelSerializer`, whose conditional keys depend on runtime `extra_data`/platform, not on static node-type declarations.)
- One shared `inspect_node_queryset()` preloads every relation the node serializers render (`select_related` + `prefetch_related`), used for both the chatbot's own pipeline and any embedded `pipeline_start` pipeline. No per-node queries.
- Custom actions render from the existing `node.custom_action_operations` reverse relation (pre-dates this branch). `params["custom_actions"]` is still parsed — **only to preserve operation ordering** — while the `CustomAction` object comes from the prefetched relation.
- The embedded `pipeline_start` pipeline is the only reference not backed by a node FK (`pipeline_id` is JSON on `EventAction.params`). It is loaded with a direct, team-scoped `Pipeline.objects` query (team from serializer context), prefetched with `inspect_node_queryset()`.

**Tech stack:** Django ORM, DRF, drf-spectacular, pytest. No new libraries.

---

## What maps to what

| Current fetcher call (in serializer) | Replacement |
|---|---|
| `fetcher.llm_provider(...)` / `fetcher.llm_provider_model(...)` | `node.llm_provider` / `node.llm_provider_model` |
| `fetcher.synthetic_voice(...)` | `node.synthetic_voice` (+ `.voice_provider`) |
| `fetcher.source_material(...)` | `node.source_material` |
| `fetcher.assistant(...)` | `node.assistant` |
| `fetcher.collection(collection_id)` | `node.collection` |
| `fetcher.collection(...)` for indexes | `node.collection_indexes.all()` |
| `fetcher.custom_action(action_id)` | `node.custom_action_operations` → `.custom_action` |
| `fetcher.embedded_pipeline(...)` | direct `Pipeline.objects.filter(team=…, id=…)` query |

Per-node-type key dropping still keys off the node **type** via `node.has_parameter(...)`, but moves from the `_ABSENT` sentinel into a single `to_representation` (so it covers the new declarative fields too).

---

## File structure

| File | Change |
|---|---|
| `apps/api/v2/inspect/nodes.py` | Add `inspect_node_queryset()`; **delete** `RESOURCE_PARAM_FIELDS` (derived in the serializer now), `ResourceKind`, `node_class_for`; keep `graph_digest`, `node_render_order` |
| `apps/api/v2/inspect/serializers.py` | `InspectNodeSerializer`: 4 resource keys → declarative nested fields, `llm`/`voice`/`custom_actions` stay method fields, `_ABSENT` → `to_representation` dropping; root `pipeline` → declarative; rename `_FetcherContextMixin` → `_TeamContextMixin`; embedded-pipeline direct query in `InspectTriggerActionSerializer.get_pipeline` |
| `apps/api/v2/inspect/versioning.py` | `_inspect_target_queryset`: `prefetch_related("pipeline__node_set")` → `Prefetch("pipeline__node_set", queryset=inspect_node_queryset())`; drop `ResourceFetcher` from docstring |
| `apps/api/v2/views.py` | Drop `ResourceFetcher` import + construction; context = `{"team": target.team}` |
| `apps/api/v2/inspect/resources.py` | **Delete** |
| `apps/api/v2/inspect/tests/test_resources.py` | **Delete** |
| `apps/api/v2/inspect/tests/test_node_rendering.py` | Replace `_FetcherStub` with FK attributes on the node stub |
| `apps/api/v2/inspect/tests/test_nodes.py` | Drop `node_class_for` tests; move the "resource params declared on some node" invariant to reference `InspectNodeSerializer._RESOURCE_PARAM_KEYS`; keep `graph_digest`/`node_render_order` |
| `apps/api/v2/tests/test_chatbots_inspect.py` | Sync FK mirror in fixtures; drop fetcher from query-count test + re-derive count; rewrite/drop the 2 cross-team tests |

**Unaffected** (verified): `test_secrets_exclusion.py` (tests collection/channel serializers directly), `test_inspect_schema.py` (static schema), `test_flattened_serializers.py` (tests Flattened* serializers directly).

---

## Task 1: Add `inspect_node_queryset()` and slim `nodes.py`

**Files:** Modify `apps/api/v2/inspect/nodes.py`

`RESOURCE_PARAM_FIELDS` is **removed** — it existed only for `get_params` stripping, and that set is now derived from `InspectNodeSerializer._CONDITIONAL_KEY_PARAMS` (Task 2), the single map of render-key → backing params. `nodes.py` keeps only the queryset + graph helpers.

- [ ] **Step 1: Rewrite `nodes.py`**

```python
from django.db.models import Prefetch

from apps.documents.models import Collection
from apps.pipelines.models import Node


def inspect_node_queryset():
    """A Node queryset with every resource relation the inspect serializers render preloaded.

    Used for the chatbot's own pipeline and any embedded pipeline_start pipeline, so a node's
    FK/M2M relations resolve without per-node queries.
    """
    return Node.objects.select_related(
        "llm_provider",
        "llm_provider_model",
        "source_material",
        "assistant",
        "synthetic_voice",
        "synthetic_voice__voice_provider",
        "collection",
        "collection__llm_provider",
        "collection__embedding_provider_model",
    ).prefetch_related(
        "collection__files",
        Prefetch(
            "collection_indexes",
            queryset=Collection.objects.select_related("llm_provider", "embedding_provider_model").prefetch_related(
                "files"
            ),
        ),
        "custom_action_operations__custom_action__auth_provider",
    )


def node_render_order(node) -> int:
    """Sort key that puts the start node first and the end node last, leaving the rest in order."""
    return {"StartNode": 0, "EndNode": 2}.get(node.type, 1)


def graph_digest(node_list, pipeline_data: dict | None) -> dict:
    """Build a lightweight view of the pipeline's shape (nodes as flow_id/type/label + edges)."""
    nodes = [{"flow_id": node.flow_id, "type": node.type, "label": node.label} for node in node_list]
    edges = [
        {
            "source": edge.get("source"),
            "target": edge.get("target"),
            "source_handle": edge.get("sourceHandle"),
            "target_handle": edge.get("targetHandle"),
        }
        for edge in (pipeline_data or {}).get("edges", [])
    ]
    return {"nodes": nodes, "edges": edges}
```

Deleted: `RESOURCE_PARAM_FIELDS`, `ResourceKind`, `node_class_for`, and the `parse_custom_actions`/`enum`/`pipeline_nodes` imports they needed.

- [ ] **Step 2: Lint** — `uv run ruff check apps/api/v2/inspect/nodes.py --fix && uv run ruff format apps/api/v2/inspect/nodes.py`

---

## Task 2: Rewrite the node serializer with declarative DRF fields

**Files:** Modify `apps/api/v2/inspect/serializers.py`

**Goal:** lean on DRF — declare the simple resource references as nested serializer fields with `source=`, keep only the genuinely composite ones as method fields, and replace the `_ABSENT` sentinel with one `to_representation`.

- [ ] **Step 1: Imports** — drop `RESOURCE_PARAM_FIELDS` from the `nodes` import (it's gone); add `inspect_node_queryset` from `nodes`; add `from django.db.models import Prefetch`; add `from apps.utils.fields import as_int`. `parse_custom_actions` import stays. The `_ABSENT` sentinel **stays** (still used by `ChannelSerializer`).

- [ ] **Step 2: Rename `_FetcherContextMixin` → `_TeamContextMixin`** (only `InspectTriggerActionSerializer` uses it now):

```python
class _TeamContextMixin:
    """Gives a serializer access to the request ``team`` in its context.

    A missing team means an inspect serializer is being used outside the inspect view — a
    programming error — so we raise instead of silently mis-scoping.
    """

    @property
    def _team(self):
        try:
            return self.context["team"]
        except KeyError:
            raise RuntimeError(
                f"{type(self).__name__} requires a 'team' in serializer context. Render inspect "
                "serializers via the inspect view (or pass context={'team': ...})."
            ) from None
```

- [ ] **Step 3: `InspectNodeSerializer` — drop the `_FetcherContextMixin` base** (it no longer needs context). Declare the **four simple references** as nested fields (DRF maps `source=` → the node's FK/M2M and handles null/`[]`); the unset-vs-absent dropping happens in `to_representation`:

```python
class InspectNodeSerializer(serializers.ModelSerializer):
    CONDITIONAL_RESPONSE_KEYS = (
        "llm", "voice", "source_material", "assistant",
        "custom_actions", "media_collection", "indexed_collections",
    )
    # Each conditional render key → the node-param field(s) that back it. A node renders a key only
    # when its type declares the backing param; otherwise to_representation drops it entirely.
    _CONDITIONAL_KEY_PARAMS = {
        "llm": ("llm_provider_id", "llm_provider_model_id"),
        "voice": ("synthetic_voice_id",),
        "source_material": ("source_material_id",),
        "assistant": ("assistant_id",),
        "custom_actions": ("custom_actions",),
        "media_collection": ("collection_id",),
        "indexed_collections": ("collection_index_ids",),
    }
    # The resource-id keys get_params strips from the rendered params blob — derived from the one
    # map above, so there's no separate list to keep in sync. (Outermost iterable is evaluated in
    # class scope, so it can see _CONDITIONAL_KEY_PARAMS.)
    _RESOURCE_PARAM_KEYS = frozenset(p for params in _CONDITIONAL_KEY_PARAMS.values() for p in params)

    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()
    params = serializers.SerializerMethodField(help_text="...")

    # Composite / grouped → method fields (flatten two sources + null-when-empty; or group ops).
    llm = serializers.SerializerMethodField()
    voice = serializers.SerializerMethodField()
    custom_actions = serializers.SerializerMethodField()

    # Simple references → declarative nested fields off the node's relations.
    source_material = SourceMaterialSerializer(allow_null=True, read_only=True)
    assistant = AssistantSerializer(allow_null=True, read_only=True)
    media_collection = MediaCollectionSerializer(source="collection", allow_null=True, read_only=True)
    indexed_collections = IndexedCollectionSerializer(source="collection_indexes", many=True, read_only=True)

    class Meta:
        model = Node
        fields = [
            "node_id", "type", "label", "params",
            "llm", "voice", "source_material", "assistant",
            "custom_actions", "media_collection", "indexed_collections",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # A node only renders the resource keys whose backing param its type declares.
        for key, params in self._CONDITIONAL_KEY_PARAMS.items():
            if key in data and not any(instance.has_parameter(p) for p in params):
                del data[key]
        return data

    @extend_schema_field(node_params_schema())
    def get_params(self, node) -> dict:
        params = {k: v for k, v in (node.params or {}).items() if k not in self._RESOURCE_PARAM_KEYS and k != "name"}
        if "max_results" in params:  # only bounds index search — surface under a clearer name
            params["max_indexed_collection_search_results"] = params.pop("max_results")
        return params

    @extend_schema_field(FlattenedLlmSerializer(allow_null=True))
    def get_llm(self, node):
        pair = ProviderModelPair.from_parts(node.llm_provider, node.llm_provider_model)
        return FlattenedLlmSerializer(pair).data if pair is not None else None

    @extend_schema_field(FlattenedVoiceSerializer(allow_null=True))
    def get_voice(self, node):
        voice = node.synthetic_voice
        if voice is None:
            return None
        return FlattenedVoiceSerializer(VoicePair(voice.voice_provider, voice)).data

    @extend_schema_field(CustomActionSerializer(many=True))
    def get_custom_actions(self, node):
        # CustomAction objects come from the prefetched relation; params is parsed only to keep the
        # operation_ids in saved order (resolved_operations drops any no longer in the schema).
        actions_by_id = {op.custom_action_id: op.custom_action for op in node.custom_action_operations.all()}
        selections = []
        for action_id, operation_ids in parse_custom_actions(node.params.get("custom_actions")):
            action = actions_by_id.get(action_id)
            if action is not None:
                selections.append(CustomActionSelection(action, operation_ids))
        return CustomActionSerializer(selections, many=True).data
```

> Notes:
> - The method fields no longer return `_ABSENT` — they return the real value (`null`/`[]`/object); `to_representation` does all the per-type dropping. This is what lets declarative and method fields share one dropping path.
> - `read_only=True` on the nested fields signals output-only intent (this serializer is never `.is_valid()`-ed).
> - `parse_custom_actions` yields **int** action ids; `op.custom_action_id` is an int FK — lookup matches.
> - `indexed_collections` renders in through-table order; all current tests use a single indexed collection so order is moot.

- [ ] **Step 4: `InspectPipelineSerializer.get_nodes`** — unchanged (`pipeline.node_set.all()` is served from the `inspect_node_queryset()` prefetch). Keep the `context=self.context` pass-through so embedded-pipeline triggers keep `team`.

- [ ] **Step 5: Root `ChatbotInspectSerializer` — make `pipeline` declarative.** `experiment.pipeline` is a nullable FK and nested serializers inherit the parent context, so the `get_pipeline` method field is unnecessary:

```python
# replaces:  pipeline = serializers.SerializerMethodField(...)  + get_pipeline()
pipeline = InspectPipelineSerializer(allow_null=True, read_only=True)
```

(`voice` and `channels` stay method fields — flatten-with-null and the `get_channels` helper, respectively.)

- [ ] **Step 6: `InspectTriggerActionSerializer`** — base it on `_TeamContextMixin`; the embedded `pipeline` stays a method field (its `pipeline_id` is JSON, not an FK):

```python
@extend_schema_field(InspectPipelineSerializer(required=False))
def get_pipeline(self, action):
    if action.action_type != EventActionType.PIPELINE_START:
        return None
    pipeline_id = as_int((action.params or {}).get("pipeline_id"))
    if pipeline_id is None:
        return None
    pipeline = (
        Pipeline.objects.filter(team=self._team, id=pipeline_id)
        .prefetch_related(Prefetch("node_set", queryset=inspect_node_queryset()))
        .first()
    )
    if pipeline is None:
        return None
    return InspectPipelineSerializer(pipeline, context=self.context).data
```

- [ ] **Step 7: Lint** — `uv run ruff check apps/api/v2/inspect/serializers.py --fix && uv run ruff format apps/api/v2/inspect/serializers.py`

> **Schema check:** declaring nested `ModelSerializer`s as real fields (vs. `extend_schema_field` on a method field) can shift generated component names/refs. `test_inspect_schema.py` validates this — run it (Task 8) and adjust `component_name`s if a ref name changes.

---

## Task 3: Wire prefetch into `versioning.py`, strip fetcher from the view

**Files:** Modify `apps/api/v2/inspect/versioning.py`, `apps/api/v2/views.py`

- [ ] **Step 1: `versioning.py`** — import `inspect_node_queryset` from `apps.api.v2.inspect.nodes`; in `_inspect_target_queryset` replace `"pipeline__node_set"` in `prefetch_related` with `Prefetch("pipeline__node_set", queryset=inspect_node_queryset())`. Update the module docstring to drop the `ResourceFetcher` references.

- [ ] **Step 2: `views.py`** — remove `from apps.api.v2.inspect.resources import ResourceFetcher` and the `fetcher = ResourceFetcher.for_experiment(target)` line; change the serializer context to `context={"team": target.team}`.

- [ ] **Step 3: Lint both files.**

---

## Task 4: Delete `ResourceFetcher`

**Files:** Delete `apps/api/v2/inspect/resources.py`, `apps/api/v2/inspect/tests/test_resources.py`

- [ ] **Step 1:** `git rm apps/api/v2/inspect/resources.py apps/api/v2/inspect/tests/test_resources.py`

- [ ] **Step 2:** Confirm nothing else imports them — `grep -rn "ResourceFetcher\|iter_resource_refs\|ResourceKind\|node_class_for\|inspect.resources" apps/ --include=*.py` returns nothing.

---

## Task 5: Fix test fixtures to sync the FK mirror

`NodeFactory.create(params=...)` does **not** run `update_from_params()`, so the FK columns / M2M / `custom_action_operations` are empty unless synced. Production always calls it (via `update_nodes_from_data`). Do this **locally in the inspect tests** — *not* a global `NodeFactory` post-generation hook, which would raise FK `IntegrityError` in unrelated tests that put non-existent ids in params.

**Files:** Modify `apps/api/v2/tests/test_chatbots_inspect.py`

- [ ] **Step 1:** Add a helper near the top of the test module:

```python
def _make_node(**kwargs):
    """Like NodeFactory.create but also runs update_from_params() — mirrors production, where
    update_nodes_from_data() syncs the FK mirror + custom_action_operations after every node write."""
    node = NodeFactory.create(**kwargs)
    node.update_from_params()
    return node
```

- [ ] **Step 2:** Replace every `NodeFactory.create(...)` in this file (in `_build_inspect_bot`, `_adversarial_bot`, and the per-test ad-hoc nodes: "Stale", "Leaky", "LeakyVoice", "Malformed", embedded "Embedded") with `_make_node(...)`.

- [ ] **Step 3: Sanity-run** the happy-path assertions — `uv run pytest apps/api/v2/tests/test_chatbots_inspect.py -k "acceptance or full_response or custom_action_wired or pipeline_start" -v`. These should pass once fixtures sync.

> `_make_node` keeps `custom_action_unknown_operation_resolves_to_absent` working: `set_custom_actions` persists a `CustomActionOperation` even for an op missing from the schema, so the action still renders and `resolved_operations` drops the unknown op → `allowed_operations: []`.

---

## Task 6: Rewrite `test_node_rendering.py` (convert to DB-backed)

The old test used a `_FetcherStub` + dataclass node. With declarative nested fields the dataclass stub turns brittle: `indexed_collections` (`many=True`) makes DRF iterate `node.collection_indexes` unless it's a `Manager`, and the nested `ModelSerializer`s attribute-access real fields. **Build real nodes instead** — it tests the actual `source=` wiring and the `to_representation` dropping.

**Files:** Modify `apps/api/v2/inspect/tests/test_node_rendering.py`

- [ ] **Step 1:** Drop `_FetcherStub` and the `_Node` dataclass. Add a `@pytest.mark.django_db` helper that creates a node with its params synced (so FK/M2M/operations populate), then renders it:

```python
def _render(type, params, **resources):
    node = NodeFactory.create(type=type, label=type, params=params)
    node.update_from_params()           # sync FK mirror + custom_action_operations (as production does)
    return InspectNodeSerializer(node).data   # no context needed
```

- [ ] **Step 2:** Port each case to real factory objects:
  - `test_start_node_carries_no_resource_keys` → a `StartNode` renders only `{node_id, type, label, params}` (all resource keys dropped by `to_representation`).
  - `test_llm_node_declares_all_keys_with_null_and_empty_when_unset` → an `LLMResponseWithPrompt` with no resource ids renders `llm/voice/source_material/media_collection = null`, `custom_actions/indexed_collections = []`, and no `assistant` key.
  - `test_params_renamed` → `max_results` → `max_indexed_collection_search_results`.
  - `test_voice_not_dropped_when_only_synthetic_voice_field_set` → set `synthetic_voice_id` to a real `SyntheticVoiceFactory` voice (with provider); assert the flattened `voice` object.

- [ ] **Step 3:** Run — `uv run pytest apps/api/v2/inspect/tests/test_node_rendering.py -v`.

> Keeping a couple of pure-function checks (e.g. the `to_representation` dropping logic) DB-free is fine if you stub `has_parameter` only — but the resource-rendering cases need real instances.

---

## Task 7: Adapt `test_nodes.py`

**Files:** Modify `apps/api/v2/inspect/tests/test_nodes.py`

- [ ] **Step 1:** Remove the imports of and tests for `node_class_for` (`test_node_class_for_resolves_known_type`, `test_node_class_for_unknown_type_is_none`).
- [ ] **Step 2:** Keep the "resource params declared on some node type" invariant, but point it at `InspectNodeSerializer._RESOURCE_PARAM_KEYS` (the strip-set now lives on the serializer). Keep `graph_digest` / `node_render_order` tests unchanged.
- [ ] **Step 3:** Run — `uv run pytest apps/api/v2/inspect/tests/test_nodes.py -v`.

---

## Task 8: Cross-team tests + query-count guard

**Files:** Modify `apps/api/v2/tests/test_chatbots_inspect.py`

- [ ] **Step 1: Query-count test** (`test_inspect_render_query_count_constant_across_versions`) — remove `from apps.api.v2.inspect.resources import ResourceFetcher` (top of file) and the `fetcher = ...` line; build the serializer with `context={"team": target.team}`. Re-derive `EXPECTED_RENDER_QUERIES` empirically (run, read the failure's actual count, set it). The constant-across-versions guarantee still holds once fixtures sync the working draft's FKs (Task 5). Note the `_adversarial_bot` nodes also become `_make_node`.

- [ ] **Step 2: Cross-team tests** — read-time team scoping is gone by decision, so rewrite to assert isolation at the **write path** rather than at inspect read:
  - `test_cross_team_resource_not_embedded` and `test_cross_team_synthetic_voice_not_embedded`: either (a) delete them and rely on the save-time validation test (Security follow-up), or (b) convert them to assert that saving a pipeline with a cross-team resource id is rejected. Pick (b) only if save-time validation is implemented in the same PR; otherwise (a) + a TODO referencing the new ADR.

- [ ] **Step 3:** Run the whole inspect suite — `uv run pytest apps/api/v2/tests/test_chatbots_inspect.py apps/api/v2/inspect/ -v --tb=short`.

---

## Task 9: Full verification

- [ ] **Step 1:** `uv run pytest apps/api/v2/ apps/pipelines/ -q --tb=short`
- [ ] **Step 2:** Type-check — `uv run ty check apps/api/v2/`
- [ ] **Step 3:** Lint/format all changed files — `uv run ruff check apps/api/v2/ --fix && uv run ruff format apps/api/v2/`
- [ ] **Step 4:** Confirm the OpenAPI schema still generates — `uv run python manage.py spectacular --validate >/dev/null` (or rely on `test_inspect_schema.py`).

---

## Security follow-up (ADR-0028)

Reading FKs directly removes the inspect API's read-time team scoping. The compensating control is **at the write path**: a cross-team resource id must not be able to enter `node.params` in the first place.

- [ ] Confirm whether pipeline-save validates that resource ids in node params belong to the team. If not, add that validation (it's the sole guard now).
- [ ] Write a short ADR (`docs/adr/`) superseding the read-time-scoping aspect of ADR-0028: the inspect response trusts the FK mirror; team isolation is enforced where params are written. Append to `docs/adr/index.md` and `mkdocs.yml` per the repo convention. (This is a new ADR, not an edit to ADR-0028.)

---

## Notes / behavioral deltas to watch

- **Custom-action operation order**: preserved (still read from params order). The `CustomAction` object is the only thing now sourced from the relation.
- **Indexed-collection order**: now through-table/insertion order rather than `params` list order. Harmless for current tests (single collection each). Document if a consumer depends on order.
- **`params` remains authoritative** — consistent with the FK-feature design. Nothing here writes params or FKs; the inspect path is read-only.
