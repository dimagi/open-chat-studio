# ADR-0046: Node rows are the sole source of pipeline node content

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-07-22</p>

## Context

A pipeline's graph is stored twice: `Node` rows hold each node's content (`type`,
`label`, `params`, derived resource FK columns), and `Pipeline.data` — the react-flow
JSON saved by the editor — embeds a full copy of that content in `data.nodes[*].data`.
The copies drift: publishing a version rewrites referenced-record ids in `Node.params`
but not in the blob, and `Node.set_params` writes the row only. `Pipeline.flow_data`
already compensated by overwriting the blob's content with row values on every read,
and one-off scrub commands existed solely to chase blob/row drift.

## Decision

We will store node content only on `Node` rows. `Pipeline.data` becomes layout-only:
per node it keeps `id`, react-flow `type`, and `position`; `edges` and `viewport` stay
as-is. Full flows served to the editor are reconstructed from the rows.

Saves are inverted: the layout-only graph is persisted first, then
`update_nodes_from_data(node_data)` reconciles the rows, taking content from an explicit
`{flow_id: {type, label, params}}` mapping instead of reading it out of `self.data`.
Only paths where content genuinely originates outside the database supply the mapping —
UI saves, pipeline creation, import files. Paths that already have rows reconstruct from
them: revert builds the mapping from the version's rows; publishing versions and copies
never calls `update_nodes_from_data` at all (rows are copied directly). A node id present
in the graph but absent from both the mapping and the rows is an error.

An idempotent data migration strips the blob from existing rows, skipping (and logging)
any row whose blob holds content with no matching `Node` row rather than destroying it.
Read paths tolerate old-format data, so pre-migration rows, restored backups, and old
import files keep working; import paths extract an embedded blob to build rows, then
persist layout-only data.

## Consequences

- One source of truth: version publish/revert, param edits, and scrub commands touch
  rows only; blob/row drift is structurally impossible for migrated rows.
- `Pipeline.data` no longer duplicates params (including large prompts), shrinking rows.
- Any consumer of node content must read rows (or `flow_data`); reading `pipeline.data`
  for params silently sees nothing.
- `update_nodes_from_data` is a breaking API: every caller must pass the mapping or
  guarantee rows exist.
- Copy-id readability (`{NodeType}-{hash}`) now requires passing node types from rows
  into `duplicate_pipeline_with_new_ids`, since the blob is gone.
- The wire format is unchanged; the frontend still sends and receives full nodes.

## Alternatives considered

- **Keep the blob, fix drift at write time** (sync both on every row write): rejected —
  every future writer must remember the dual write; drift remains possible.
- **Drop `Node` rows and make the blob authoritative**: rejected — rows carry queryable
  FK mirrors, per-node versioning/archiving, and admin/API surfaces the blob cannot.
- **Strip only `params`, keep `label`/inner `type` in the blob**: rejected — leaves two
  owners for the remaining fields; same class of drift for no benefit.

## References

- Implementation companion: `docs/design/pipeline-data-layout-only.md`
