# ADR-0048: Node rows own pipeline layout; `Pipeline.data` is edges only

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-07-24</p>

Extends: [ADR-0046](0046-layout-only-pipeline-data.md)

## Context

ADR-0046 made the `Node` rows the sole source of node *content* and reduced
`Pipeline.data` to a layout blob: per node an `id`, react-flow `type` and `position`,
plus `edges` and `viewport`. Position was the only per-node value still living in the
blob, and the react-flow `type` is derivable from `Node.type` (`StartNode` → `startNode`,
`EndNode` → `endNode`, everything else → `pipelineNode`). Keeping the `nodes` list in the
blob meant every read still reconciled two node lists, and layout could drift from the
rows exactly as content used to.

A prior phase added nullable `position_x`/`position_y` columns to `Node` and shadow-wrote
them on every save, so the rows already carry layout. This ADR completes the move.

## Decision

We will remove node information from `Pipeline.data` entirely: it becomes `{edges,
viewport}` with no `nodes` key. The `Node` rows are the sole source of layout as well as
content — position from the `position_x`/`position_y` columns, react-flow type derived
from `Node.type`.

- `Pipeline.flow_data` rebuilds the full react-flow graph from the rows (position and type
  from each row); it reads only `edges`/`viewport` from `Pipeline.data`.
- Saves supply a *complete* membership mapping to `update_nodes_from_data(node_data)`: its
  keys are every node id in the graph, each value either content (create/update the row,
  writing its position) or `None` (membership only — the row must exist and is left
  untouched). Removed rows are deleted, or archived when they have versions.
- The PATCH engine works off `flow_data` (merged with the stored `viewport`) since
  `Pipeline.data` no longer lists nodes; it returns the layout-only blob and the complete
  mapping.
- `duplicate_pipeline_with_new_ids` takes the id→type mapping from the rows, generates new
  ids and rewrites edges — it no longer reads a node list from the blob.
- The position backfill and `nodes`-key strip already ran against every pipeline via a
  one-off command ahead of this change, so no data migration ships with it — the read
  switch below assumes every row already carries its position.

The wire format is unchanged: the editor still sends and receives full nodes.

## Consequences

- `Pipeline.data` no longer duplicates any node state; layout/content drift is structurally
  impossible for migrated rows, and `flow_data`'s old missing-node `KeyError` (a row absent
  from `data["nodes"]`) is gone because the rows drive the read.
- Position columns stay nullable; an un-backfilled row renders at the origin until its next
  save. The one-off backfill command already healed any writer that bypassed the
  shadow-write (e.g. revert) as of this ADR; drift introduced afterwards needs a new data
  migration, since there is no command left to rerun.
- `update_nodes_from_data` changed signature again (values may be `None`); every caller and
  test passes a complete mapping.
- Making the position columns non-null is deferred to a follow-up once the backfill has run
  everywhere.

## Alternatives considered

- **Keep the `nodes` list in the blob for layout**: rejected — leaves layout with two owners
  and the same drift class ADR-0046 removed for content, for no benefit now that the columns
  exist.
- **Pass graph membership separately from the content mapping**: rejected — a single complete
  mapping keeps one argument as the authority for both membership and content.
- **Make the position columns non-null in this change**: rejected — requires the backfill to
  have run on every environment first; sequenced as a later ADR.
