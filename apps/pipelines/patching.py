"""In-memory graph patch engine for incremental pipeline saves.

Applies semantic diffs to Pipeline.data entirely in memory.
Never touches the database directly — the caller (the PATCH view) is responsible
for persisting the merged graph and calling update_nodes_from_data().
"""

from apps.pipelines.flow import EdgeDiff, Flow, NodeDiff, PipelineDiffPayload, split_flow_data


def apply_pipeline_patch(current_data: dict, patch: PipelineDiffPayload) -> tuple[dict, dict[str, dict]]:
    """Apply a semantic graph diff to ``current_data`` and return ``(layout_data, node_data)``.

    ``layout_data`` is the complete merged graph in the persisted layout-only format
    (node content stripped, see ADR-0046) and can be assigned to ``Pipeline.data``.
    Node and edge objects not mentioned in the patch are preserved unchanged.

    ``node_data`` carries content only for the patch's update nodes and the adds that
    actually entered the graph (duplicate adds are skipped entirely), ready for
    ``update_nodes_from_data(node_data)`` — which the caller must still invoke after
    saving. Content blobs still embedded in old-format ``current_data`` are stripped,
    never promoted to node content: the Node rows own it.
    """
    # Preserve any keys that Flow.model_dump() may drop (e.g. viewport)
    flow = Flow(**current_data)
    existing_node_ids = {node.id for node in flow.nodes}

    _apply_node_diff(flow, patch.nodes)
    _apply_edge_diff(flow, patch.edges)

    merged = flow.model_dump()
    # model_dump only includes fields defined on the Flow model.
    # Preserve extra keys like viewport.
    for key in current_data:
        if key not in merged:
            merged[key] = current_data[key]
    layout_data, _ = split_flow_data(merged)
    # An add for an id already in the graph is skipped by _apply_node_diff (idempotent
    # retry), so its content must not overwrite the existing Node row either — unless the
    # same patch deletes that id first, which makes the add a genuine replacement.
    deleted_ids = set(patch.nodes.delete)
    content_nodes = {node.id: node for node in patch.nodes.update}
    for node in patch.nodes.add:
        if node.id not in existing_node_ids or node.id in deleted_ids:
            content_nodes.setdefault(node.id, node)
    node_data = {
        node.id: {"type": node.data.type, "label": node.data.label, "params": node.data.params}
        for node in content_nodes.values()
        if node.data
    }
    return layout_data, node_data


def _apply_node_diff(flow: Flow, diff: NodeDiff) -> None:
    node_map = {node.id: node for node in flow.nodes}

    # Delete: remove by id
    for node_id in diff.delete:
        node_map.pop(node_id, None)

    # Update: replace in-place
    for updated in diff.update:
        node_map[updated.id] = updated

    # Add: insert, skip if already present (idempotent)
    for added in diff.add:
        if added.id not in node_map:
            node_map[added.id] = added

    flow.nodes = list(node_map.values())

    # Cull edges that referenced a deleted node
    deleted_ids = set(diff.delete)
    if deleted_ids:
        flow.edges = [edge for edge in flow.edges if edge.source not in deleted_ids and edge.target not in deleted_ids]


def _apply_edge_diff(flow: Flow, diff: EdgeDiff) -> None:
    edge_map = {edge.id: edge for edge in flow.edges}

    # Delete: remove by id
    for edge_id in diff.delete:
        edge_map.pop(edge_id, None)

    # Update: replace in-place
    for updated in diff.update:
        edge_map[updated.id] = updated

    # Add: insert, skip if already present (idempotent)
    for added in diff.add:
        if added.id not in edge_map:
            edge_map[added.id] = added

    flow.edges = list(edge_map.values())
