"""In-memory graph patch engine for incremental pipeline saves.

Applies semantic diffs to Pipeline.data entirely in memory.
Never touches the database directly — the caller (the PATCH view) is responsible
for persisting the merged graph and calling update_nodes_from_data().
"""

from apps.pipelines.build_state import output_handle_labels, rewired_edges_for_node
from apps.pipelines.flow import (
    EdgeDiff,
    Flow,
    FlowNode,
    FlowWithoutNodes,
    NodeDiff,
    PipelineDiffPayload,
    split_flow_data,
)


def apply_pipeline_patch(
    current_flow: dict, patch: PipelineDiffPayload
) -> tuple[FlowWithoutNodes, dict[str, FlowNode | None]]:
    """Apply a semantic graph diff to ``current_flow`` and return ``(edge_data, node_data)``.

    ``current_flow`` is the full current graph — ``Pipeline.flow_data``, whose nodes are
    rebuilt from the rows because ``Pipeline.data`` no longer lists them (ADR-0049).

    ``edge_data`` is the merged graph minus its nodes, ready to be dumped into
    ``Pipeline.data``.

    ``node_data`` is the complete membership of the merged graph, ready for
    ``update_nodes_from_data(node_data)`` — which the caller must still invoke after saving.
    The patch's update nodes and the adds that actually entered the graph carry content
    (with their position, written to the row's position columns); every other node maps to
    ``None`` (membership only, its row left untouched). Duplicate adds are skipped, so a
    retried add cannot mutate an existing row.
    """
    flow = Flow(**current_flow)
    existing_node_ids = {node.id for node in flow.nodes}

    handle_changes = _apply_node_diff(flow, patch.nodes)
    _apply_edge_diff(flow, patch.edges)
    _rewire_edges(flow, handle_changes)

    edge_data, _ = split_flow_data(flow)

    return edge_data, _collect_node_data(flow, patch, existing_node_ids)


#: ``(node_id, before, after)`` for one node whose output handles moved -- see _apply_node_diff.
HandleChange = tuple[str, dict[str, str | None], dict[str, str | None]]


def _apply_node_diff(flow: Flow, diff: NodeDiff) -> list[HandleChange]:
    """Merge ``diff`` into ``flow.nodes`` and report which updates moved a node's output handles.

    The edge rewiring those changes need runs later, over the fully-merged edge set; see
    apply_pipeline_patch and _rewire_edges.
    """
    node_map = {node.id: node for node in flow.nodes}

    # Delete: remove by id
    for node_id in diff.delete:
        node_map.pop(node_id, None)

    # Update: replace in-place, noting any whose output handles moved
    handle_changes: list[HandleChange] = []
    for updated in diff.update:
        previous = node_map.get(updated.id)
        node_map[updated.id] = updated
        if change := _handle_change_for_update(previous, updated):
            handle_changes.append(change)

    # Add: insert, skip if already present (idempotent)
    for added in diff.add:
        if added.id not in node_map:
            node_map[added.id] = added

    flow.nodes = list(node_map.values())

    # Cull edges that referenced a deleted node
    deleted_ids = set(diff.delete)
    if deleted_ids:
        flow.edges = [edge for edge in flow.edges if edge.source not in deleted_ids and edge.target not in deleted_ids]

    return handle_changes


def _handle_change_for_update(previous: FlowNode | None, updated: FlowNode) -> HandleChange | None:
    """Whether this update moved ``updated``'s output handles, and what moved if so.

    Handles are a pure function of type and params, so a drag, a selection, or a label edit
    never reaches ``output_handle_labels`` -- only a type or param change can validate a router
    through ``model_validate()``.
    """
    previous_data = previous.data if previous else None
    if previous_data is None or updated.data is None:
        return None
    if previous_data.type == updated.data.type and previous_data.params == updated.data.params:
        return None
    before = output_handle_labels(previous_data)
    after = output_handle_labels(updated.data)
    if before == after:
        return None
    return (updated.id, before, after)


def _rewire_edges(flow: Flow, handle_changes: list[HandleChange]) -> None:
    """Follow or drop each changed node's outgoing edges, over the fully-merged edge set.

    Runs last -- after both diffs are applied -- so it has the final say: an edge the same patch
    also adds or updates is rewired or dropped along with the rest instead of escaping validation
    by not existing yet when this ran, and a stale edge-diff update naming a handle this drops
    cannot resurrect it, because this runs after that update already landed.
    """
    for node_id, before, after in handle_changes:
        changed, deleted_ids = rewired_edges_for_node(flow.edges, node_id, before, after)
        if not changed and not deleted_ids:
            continue
        changed_by_id = {edge.id: edge for edge in changed}
        deleted = set(deleted_ids)
        flow.edges = [changed_by_id.get(edge.id, edge) for edge in flow.edges if edge.id not in deleted]


def _apply_edge_diff(flow: Flow, diff: EdgeDiff) -> None:
    edge_map = {edge.id: edge for edge in flow.edges}

    # Delete: remove by id
    for edge_id in diff.delete:
        edge_map.pop(edge_id, None)

    # Update: replace in-place (an upsert, like add below -- the rewiring above runs after this
    # and has the final say over any handle it changes; see _rewire_edges)
    for updated in diff.update:
        edge_map[updated.id] = updated

    # Add: insert, skip if already present (idempotent)
    for added in diff.add:
        if added.id not in edge_map:
            edge_map[added.id] = added

    flow.edges = list(edge_map.values())


def _collect_node_data(
    flow: Flow, patch: PipelineDiffPayload, existing_node_ids: set[str]
) -> dict[str, FlowNode | None]:
    """Complete membership mapping for the merged graph: content where the patch carries it,
    ``None`` (membership only) everywhere else.

    An add for an id already in the graph is skipped by _apply_node_diff (idempotent
    retry), so its content must not overwrite the existing Node row either — unless the
    same patch deletes that id first, which makes the add a genuine replacement.
    """
    deleted_ids = set(patch.nodes.delete)
    content_nodes = {node.id: node for node in patch.nodes.update}
    for node in patch.nodes.add:
        if node.id not in existing_node_ids or node.id in deleted_ids:
            content_nodes.setdefault(node.id, node)

    node_data: dict[str, FlowNode | None] = {}
    for node in flow.nodes:
        source = content_nodes.get(node.id)
        node_data[node.id] = source if source is not None and source.data is not None else None
    return node_data
