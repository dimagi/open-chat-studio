"""In-memory graph patch engine for incremental pipeline saves.

Applies semantic diffs to Pipeline.data entirely in memory.
Never touches the database directly — the caller (the PATCH view) is responsible
for persisting the merged graph and calling update_nodes_from_data().
"""

from apps.pipelines.flow import EdgeDiff, Flow, NodeDiff, PipelineDiffPayload, split_flow_data


def apply_pipeline_patch(current_flow: dict, patch: PipelineDiffPayload) -> tuple[dict, dict[str, dict | None]]:
    """Apply a semantic graph diff to ``current_flow`` and return ``(layout_data, node_data)``.

    ``current_flow`` is the full current graph — ``Pipeline.flow_data`` (nodes rebuilt from
    the rows) merged with any stored top-level keys (viewport) — because ``Pipeline.data``
    no longer lists nodes (ADR-0047).

    ``layout_data`` is the merged graph with the ``nodes`` key dropped (edges, viewport and
    unknown top-level keys preserved) and can be assigned to ``Pipeline.data``.

    ``node_data`` is the complete membership of the merged graph, ready for
    ``update_nodes_from_data(node_data)`` — which the caller must still invoke after saving.
    The patch's update nodes and the adds that actually entered the graph carry content
    (with their position, written to the row's position columns); every other node maps to
    ``None`` (membership only, its row left untouched). Duplicate adds are skipped, so a
    retried add cannot mutate an existing row.
    """
    flow = Flow(**current_flow)
    existing_node_ids = {node.id for node in flow.nodes}

    _apply_node_diff(flow, patch.nodes)
    _apply_edge_diff(flow, patch.edges)

    merged = flow.model_dump()
    # model_dump only includes fields defined on the Flow model.
    # Preserve extra keys like viewport.
    for key in current_flow:
        if key not in merged:
            merged[key] = current_flow[key]
    layout_data, _ = split_flow_data(merged)

    return layout_data, _collect_node_data(flow, patch, existing_node_ids)


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


def _collect_node_data(flow: Flow, patch: PipelineDiffPayload, existing_node_ids: set[str]) -> dict[str, dict | None]:
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

    node_data: dict[str, dict | None] = {}
    for node in flow.nodes:
        source = content_nodes.get(node.id)
        if source is not None and source.data is not None:
            node_data[node.id] = {
                "type": source.data.type,
                "label": source.data.label,
                "params": source.data.params,
                "position": source.position,
            }
        else:
            node_data[node.id] = None
    return node_data
