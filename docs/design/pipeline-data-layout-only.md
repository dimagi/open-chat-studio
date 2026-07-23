---
status: extracted
---

# Layout-only `Pipeline.data`

> Mini spec for making the `Node` model the single source of truth for pipeline node
> content, reducing `Pipeline.data` to graph layout only.
> Reviewed against the codebase 2026-07-22; review findings are folded in below.
> `status: extracted` — the decision is canonically recorded as
> [ADR-0046](../adr/0046-layout-only-pipeline-data.md) (cite that). This document is
> retained as the implementation companion: the contract, per-caller table, and
> migration rules are the HOW that the ADR deliberately omits.

## TL;DR

`Pipeline.data` currently stores a full copy of every node's content (`type`, `label`,
`params`) inside `data.nodes[*].data`, duplicating the `Node` rows. The two drift apart:
versioning rewrites `Node.params` but not the blob, so `Pipeline.flow_data` already has to
patch params back in from the rows. We remove the duplication: `Pipeline.data` keeps only
layout (node `id`, react-flow `type`, `position`, plus `edges` and `viewport`), and every
full-flow read reconstructs node content from the `Node` rows. Saves are inverted: the
layout-only graph is persisted first, then `update_nodes_from_data(node_data)` reconciles
the `Node` rows, taking content from an explicit mapping instead of from `self.data`.
An idempotent data migration strips the blob from existing rows.

## Current state

- `Pipeline.data` (JSON) holds the react-flow graph as sent by the frontend: nodes with an
  embedded `data` key (`{id, type, label, params}`), edges, viewport.
- `Node` rows (`apps/pipelines/models.py`) hold `flow_id`, `type`, `label`, `params` plus
  derived resource FK columns.
- `Pipeline.update_nodes_from_data()` reads `self.data["nodes"]` and syncs `Node` rows
  from the embedded blobs (its docstring wrongly says "data coming from the frontend";
  it reads the already-persisted `self.data`).
- `Pipeline.flow_data` parses `self.data`, then **replaces** each node's content with
  values from the `Node` rows — evidence the rows are already authoritative.
- Divergence sources: `Node.create_new_version` rewrites versioned param ids on the row
  only; `Node.set_params` writes the row only.

## Target format

`Pipeline.data` after this change:

```json
{
  "nodes": [
    {"id": "f30271b4-…", "type": "startNode", "position": {"x": 100, "y": 200}},
    {"id": "LLMResponseWithPrompt-48bea", "type": "pipelineNode", "position": {"x": 300, "y": 0}},
    {"id": "d14fc3bd-…", "type": "endNode", "position": {"x": 800, "y": 200}}
  ],
  "edges": [ …unchanged… ],
  "viewport": { …preserved when present… }
}
```

The per-node `data` key is never persisted. `id`, `type` (react-flow node type:
`startNode` | `pipelineNode` | `endNode`), and `position` are the only persisted node keys.

Note: unknown top-level keys (`viewport`) are preserved by the PATCH path today
(`apply_pipeline_patch`), but the POST path already drops them
(`views.py: pipeline.data = data.data.model_dump()` — `Flow` has no `viewport` field) and
injects `errors: {}`. So "viewport preserved" means: never *newly* dropped by this change;
code must not assume it is present.

## Contract

### Source of truth

| Concern | Owner |
|---|---|
| Node content: node type, label, params | `Node` rows |
| Node existence in the graph | `Pipeline.data["nodes"]` ids (must equal non-archived `node_set` flow_ids) |
| Layout: position, react-flow type | `Pipeline.data["nodes"]` |
| Edges, viewport | `Pipeline.data` |

### Read path

- `Pipeline.flow_data` (unchanged in spirit): parse layout from `self.data`, build each
  `FlowNode`'s `data` from the `Node` row (`type`, `label`, `params`), merge with the
  layout node's `position`/`type`. The stored blob is no longer read at all. Reads must
  tolerate *old-format* stored data (pre-migration rows, imported files): an embedded
  `data` key is simply ignored.
- `Pipeline.data_without_positions` currently serves raw `data` (with params) to the
  chatbot-widget page context (`pipeline_structure`, consumed by the
  `open-chat-studio-widget` component via `templates/web/app/app_base.html`). It must be
  rebuilt as `{**self.data, "nodes": <flow_data nodes, minus position>}` so params keep
  flowing to the widget from the authoritative rows while the top-level shape (viewport
  when present, no injected `errors` key) stays as stored.
- `PipelineGraph.build_from_pipeline` already reads node content from `node_set` and only
  edges from `data` — no change. Same for `apps/chat/bots.py` and the v2 inspect API
  (`apps/api/v2/inspect/`): they read node content from rows, edges from `data`.

### Write path (inverted save)

For any flow that persists a graph:

1. Parse the incoming full flow (UI payload, import file, or code-constructed nodes).
2. Split it: `layout_data` (stripped) and `node_data = {flow_id: {"type", "label", "params"}}`.
3. Persist `pipeline.data = layout_data` (preserving unknown top-level keys the input had).
4. Call `pipeline.update_nodes_from_data(node_data)`.

A shared helper performs the split (proposed: `split_flow_data(data) -> (layout_data,
node_data)` in `apps/pipelines/flow.py`). It accepts both old-format (blob embedded) and
new-format (already layout-only) input; nodes without an embedded `data` key simply
contribute no `node_data` entry.

### `update_nodes_from_data(node_data: dict[str, dict])`

New required semantics (docstring rewritten accordingly — the current one is wrong even
today):

- Node ids come from `self.data["nodes"]` — used, as today, to delete removed nodes
  (archive when they have versions) and to know the full graph membership.
- For each id present in `node_data`: `update_or_create` the `Node` row with the mapped
  `type`/`label`/`params`, then `update_from_params()` (unchanged).
- For each id in `self.data["nodes"]` but **not** in `node_data`: the `Node` row must
  already exist and is left untouched (content stays authoritative on the row). If no row
  exists, raise — the graph references a node whose content nobody supplied.
- `node_data` never comes from `self.data`; content flows only through the argument.

The "not in mapping" case is what PATCH saves rely on. This is a breaking signature
change: **all** callers (including ~12 test/factory call sites) must be updated in the
same change.

### Where `node_data` comes from, per caller

| Caller | Source of `node_data` |
|---|---|
| `views._handle_pipeline_post` | Split from the `FlowPipelineData` payload (UI) |
| `views._handle_pipeline_patch` / `apply_pipeline_patch` | Split from the diff's `add` + `update` nodes (UI); unchanged nodes aren't touched |
| `Pipeline.revert_to_version` | Rebuilt from **every** `version.node_set` row (a partial mapping would silently keep stale working params), with versioned param ids remapped back to working ids via `get_versioned_param_specs` (as today); layout comes from `version.data` |
| `helper._create_pipeline` (via `create_pipeline_with_nodes` / `create_default`) | The code-constructed `FlowNode`s, split before persisting |
| `management/commands/import_pipeline.py` | Split from the imported JSON file (old or new format) |
| `apps/utils/factories/pipelines.py` | Split from the factory's flow definition |

### Wire validation

`FlowNode.data` becomes optional (`FlowNodeData | None = None`) so layout-only stored data
parses. POST/PATCH payloads are not separately hardened: a payload node arriving without
`data` yields no `node_data` entry, and `update_nodes_from_data` raises unless a `Node`
row already exists for that id. That keeps validation in one place instead of a parallel
strict wire model.

### Versioning — no call

`Pipeline.create_new_version` keeps copying `data` verbatim (now layout-only) and
versioning `Node` rows directly via `Node.create_new_version`; `update_nodes_from_data`
stays out of that path.

`duplicate_pipeline_with_new_ids` (copy path) needs real changes, not just cleanup:

- `node["data"]["id"] = new_id` (helper.py:24) would **KeyError** on layout-only data — remove.
- The params-name rewrite (helper.py:26-27) is redundant
  (`Node.create_new_version(is_copy=True, new_flow_id=…)` already renames) — remove.
- New-id format currently derives from the blob's node type
  (`f"{data_type}-{uuid4().hex[:5]}"`). To preserve human-readable ids, the function takes
  a `node_types: dict[flow_id, node_type]` argument built from `self.node_set`
  (`values_list("flow_id", "type")`) and uses it instead of the blob.

## Other call sites to update

| Location | Change |
|---|---|
| `service_providers/llm_service/default_models._update_pipeline_node_param` | **Mandatory** (would KeyError on layout-only data): drop the `pipeline.data` write; keep `node.set_params`. Also reached via `apps/data_migrations/management/commands/remove_deprecated_models.py` |
| `custom_actions/management/commands/cleanup_stale_custom_action_refs` | Drop the `Pipeline.data` scrub phase (`_scrub_pipelines`); `Node.params` scrub remains |
| `teams/export/importer.remap_pipeline_data` | Keep for old export files (params embedded); additionally strip the blob so imported pipelines land in the new format. Node rows are imported separately (see `manifest.py`) and remain the content source |
| `experiments/management/commands/duplicate_shared_pipelines` | No functional change needed (copies `data` verbatim and recreates rows via `set_params`), listed for inventory completeness |
| `apps/pipelines/migrations/utils/migrate_start_end_nodes.py` | Historical migration helper reading the blob — leave untouched; it only runs inside its own (earlier) migration against old-format data |

## Data migration

New migration in `apps/pipelines/migrations/` (ordered after all existing ones, so the
historical `migrate_start_end_nodes` utils never see new-format data):

- Iterates all `Pipeline` rows (working versions, published versions, archived) in
  batches with `bulk_update`.
- For each node in `data["nodes"]`, keeps only `{id, type, position}` (each key only when
  present); drops everything else (notably `data`). Edges, viewport, `errors`, unknown
  top-level keys untouched.
- Defensive handling (all formats observed in code): `data` is `None`/empty, `"nodes"`
  key missing, node missing `"position"` or top-level `"type"`, node already stripped.
- **Drift guard:** if a node blob contains params and no matching `Node` row exists for
  that (pipeline, flow_id) — a known bad state, see `migrate_start_end_nodes.py`'s
  logging — the migration **skips that pipeline** and logs it, rather than destroy the
  only copy of the content. Read paths tolerate old-format rows, so skipping is safe;
  skipped rows can be healed manually.
- Idempotent: rows already in the new format produce no change; safe to rerun.
- Reversible for real, not as a no-op: pre-ADR-0046 code *requires* the blob
  (`FlowNode.data` was a mandatory field), so a code rollback needs it restored. The
  reverse rebuilds each node's blob from its `Node` row (the rows own the content and
  are untouched by the forward migration); nodes without a backing row pass through.

## Backward compatibility

- **Frontend:** wire format unchanged in both directions. GET still returns full nodes
  (reconstructed via `flow_data`, as today); POST/PATCH still send full nodes (stripped
  server-side). Verified: all pipeline reads in `assets/javascript/apps/pipeline/` go
  through the views that serve `flow_data`.
- **Old stored rows** (pre-migration, restored backups, skipped-by-drift-guard): read
  paths ignore the blob; the next save or a migration rerun strips it.
- **Old import files** (team exports, `import_pipeline` JSON): the split helper extracts
  the embedded blob to build `Node` rows, then persists layout-only data.

## Test plan (high level)

- `update_nodes_from_data`: content from mapping; untouched when id absent from mapping;
  raise when id has neither mapping entry nor row; add/remove/archive behavior preserved.
- Split helper: old-format and new-format input; unknown top-level keys preserved.
- POST/PATCH views: persisted `data` contains no node blobs; `Node` rows updated; GET
  response unchanged shape (params present, reconstructed).
- `flow_data` with layout-only and old-format stored data.
- Revert: params/type/label restored from version rows; layout from version data; blob-free.
- Copy/version: layout-only data propagates; copy ids keep the `{NodeType}-{hash}` format;
  copy renames still work without the helper's params rewrite.
- Widget context (`data_without_positions`): params present, positions absent, top-level
  shape as stored (no injected `errors`).
- Migration: idempotency (run twice), old-format converted, new-format untouched,
  drift-guard skips and logs.
- Import paths: old-format file produces new-format row with correct `Node` rows.

## Out of scope

- Any frontend change.
- Renaming `update_nodes_from_data` or `Pipeline.data`.
- Changing versioning/copy row semantics.
- Fixing the pre-existing `flow_data` KeyError when a non-archived `Node` row's flow_id
  is missing from `data["nodes"]` (reverse drift) — unchanged by this work. Note the
  blast radius grows slightly: `data_without_positions` now goes through `flow_data`,
  so the widget page context shares that failure mode (it previously served raw data).
