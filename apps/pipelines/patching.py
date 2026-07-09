"""In-memory graph patch engine for incremental pipeline saves.

Applies semantic diffs to Pipeline.data entirely in memory.
Never touches the database directly — the caller (the PATCH view) is responsible
for persisting the merged graph and calling update_nodes_from_data().
"""

from apps.pipelines.flow import EdgeDiff, Flow, NodeDiff, PipelineDiffPayload


def apply_pipeline_patch(current_data: dict, patch: PipelineDiffPayload) -> dict:
    """Apply a semantic graph diff to ``current_data`` and return the complete merged graph.

    The returned dict is a valid ``Pipeline.data`` value and can be assigned directly.
    Node and edge objects not mentioned in the patch are preserved unchanged.

    Important: the caller must still call ``update_nodes_from_data()`` after saving
    the merged graph to synchronise the database Node rows.
    """
    # Preserve any keys that Flow.model_dump() may drop (e.g. viewport)
    flow = Flow(**current_data)

    _apply_node_diff(flow, patch.nodes)
    _apply_edge_diff(flow, patch.edges)

    merged = flow.model_dump()
    # model_dump only includes fields defined on the Flow model.
    # Preserve extra keys like viewport.
    for key in current_data:
        if key not in merged:
            merged[key] = current_data[key]
    return merged


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
