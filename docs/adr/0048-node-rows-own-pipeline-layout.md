# ADR-0048: Node rows own pipeline layout; `Pipeline.data` keeps only edges and viewport

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

We will remove node information from `Pipeline.data` entirely: it becomes `{edges, viewport}`
with no `nodes` key (plus a vestigial `errors` key, still written on every save). The `Node`
rows are the sole source of layout as well as content — position from the
`position_x`/`position_y` columns, react-flow type derived from `Node.type`.

- `Pipeline.flow_data` rebuilds the full react-flow graph from the rows (position and type
  from each row); it reads only `edges`/`viewport` from `Pipeline.data`.
- Saves supply a *complete* membership mapping to `update_nodes_from_data(node_data)`: its
  keys are every node id in the graph, each value either content (create/update the row,
  writing its position) or `None` (membership only — the row must exist and is left
  untouched). Removed rows are deleted, or archived when they have versions.
- The PATCH engine works off `flow_data` since `Pipeline.data` no longer lists nodes;
  `flow_data` passes the stored `viewport` straight through, so no separate merge is needed.
  It returns the layout-only blob and the complete mapping.
- `duplicate_pipeline_with_new_ids` takes the id→type mapping from the rows, generates new
  ids and rewrites edges — it no longer reads a node list from the blob.
- Migration `pipelines.0030_strip_node_data` backfills `position_x`/`position_y` from the
  blob and then drops the `nodes` key, so the layout reaches the rows in the same deploy
  that switches reads to them. Its reverse rebuilds the full `nodes` list from the rows, so
  a code rollback remains possible. The backfill is the load-bearing half; the strip is
  housekeeping, since reads already ignore a `nodes` key and every save drops it.
  Pipelines whose blob holds content with no backing row are skipped by a drift guard and
  keep their `nodes` key, so "no `nodes` key" holds for every pipeline the migration
  touched, not literally all of them.

The wire format is unchanged: the editor still sends and receives full nodes. (GET responses
now carry `viewport` inside `data`, which they previously did not — additive, and the editor
does not read it.)

## Consequences

- `Pipeline.data` no longer duplicates any node state; layout/content drift is structurally
  impossible for migrated rows, and `flow_data`'s old missing-node `KeyError` (a row absent
  from `data["nodes"]`) is gone because the rows drive the read.
- Position columns stay nullable, so migration 0030 is a hard prerequisite for the read
  switch rather than a convenience. A row it cannot fill (no usable position in the blob)
  renders at the origin, and the first save of that pipeline drops the `nodes` key — after
  which the pre-migration layout is gone for good, since a read serves the origin and the
  editor only sends positions it sees change.
- Layout drift introduced *after* the migration (a writer that bypasses the shadow-write)
  needs a new data migration to heal: 0030 runs once at deploy and there is no command to
  rerun.
- The strip half constrains deploy ordering. Once a pipeline's `nodes` key is gone, pre-0048
  code cannot parse its `Pipeline.data` (`nodes` is required there), so the editor's
  GET/POST/PATCH return 500 for it and the widget page context loses its nodes. If migrations
  run ahead of the new code, that is the window. Chat and pipeline execution are unaffected,
  since `PipelineGraph` reads `edges` plus the rows. Rolling the code back requires
  unapplying 0030 so the blobs are rebuilt.
- `update_nodes_from_data` changed signature again (values may be `None`); every caller and
  test passes a complete mapping. Because an incomplete mapping means "delete the rest", it
  validates the mapping before removing anything and runs in a single transaction.
- Making the position columns non-null is deferred to a follow-up, gated on migration 0030
  having landed everywhere.

## Alternatives considered

- **Keep the `nodes` list in the blob for layout**: rejected — leaves layout with two owners
  and the same drift class ADR-0046 removed for content, for no benefit now that the columns
  exist.
- **Pass graph membership separately from the content mapping**: rejected — a single complete
  mapping keeps one argument as the authority for both membership and content.
- **Make the position columns non-null in this change**: rejected — requires the backfill to
  have run on every environment first; sequenced as a later ADR.
- **Ship no migration, relying on the one-off backfill command having already been run**:
  rejected. The command was only ever invoked by hand, the position columns are days old so
  the shadow-write has populated almost nothing organically, and a self-hosted deployment
  would upgrade with NULL positions. Reads would then serve the origin and the first save
  would drop the `nodes` key holding the real coordinates — permanent layout loss, with no
  command left to repair it. The migration is idempotent and cheap where the backfill has
  already run, so the asymmetry is one-sided.
