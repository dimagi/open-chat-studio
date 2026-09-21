"""How an edge follows a node's output handles across an edit.

Handles are positional (``output_i`` serves ``keywords[i]``), so dropping the second of three
keywords renumbers the third rather than freeing a slot: going by position alone would hand the
third branch's target to the second. Old handles are matched to new ones by branch label instead.
"""

from apps.pipelines.flow import FlowEdge, FlowNodeData

#: A node's output handles as ``{handle: branch label}`` -- for matching handles across an edit.
OutputHandles = dict[str, str | None]


def output_handle_labels(content: FlowNodeData) -> OutputHandles:
    """``FlowNodeData`` -> ``{handle: label}``, the shape ``handle_remap`` matches across an edit."""
    return {
        handle["handle"]: handle["label"] for handle in content.node_type.output_handles(content.params, content.id)
    }


def handle_remap(before: OutputHandles, after: OutputHandles) -> dict[str, str]:
    """Where each handle a node used to offer has ended up, keyed by the handle it was.

    A handle whose branch the edit removed is absent: its edge has nowhere to go. A rename counts
    as a removal -- inheriting the old branch's target would wire the new one somewhere nobody
    chose.
    """
    if _labels_are_distinct(before) and _labels_are_distinct(after):
        destinations = {label: handle for handle, label in after.items()}
        return {handle: destinations[label] for handle, label in before.items() if label in destinations}
    # Duplicate branch labels: keywords have to be unique, but a router that breaks that is still
    # writable, and which edge belongs to which of two identical branches is a guess. So handles
    # are followed by position instead, and only an edge left with no handle at all is dropped.
    return {handle: handle for handle in before if handle in after}


def _labels_are_distinct(handles: OutputHandles) -> bool:
    """Whether every handle in the map carries a different branch label."""
    return len(set(handles.values())) == len(handles)


def rewired_edges_for_node(
    edges: list[FlowEdge], node_id: str, before: OutputHandles, after: OutputHandles
) -> tuple[list[FlowEdge], list[str]]:
    """Edges sourced from ``node_id`` after its output handles change from ``before`` to ``after``.

    Returns ``(updated, deleted_ids)``: ``updated`` are copies of the affected edges with
    ``sourceHandle`` moved to follow the same branch label; ``deleted_ids`` are edges whose branch
    is gone. An edge not sourced from this node, or already on a handle the node didn't offer
    before the change, is left out of both -- it is either unaffected, or was already stranded
    before this edit and is not this edit's problem to clean up.
    """
    moved_to = handle_remap(before, after)
    updated: list[FlowEdge] = []
    deleted: list[str] = []
    for edge in edges:
        handle = edge.source_handle_name
        if edge.source != node_id or handle not in before:
            continue
        destination = moved_to.get(handle)
        if destination is None:
            deleted.append(edge.id)
        elif destination != handle:
            updated.append(edge.model_copy(update={"sourceHandle": destination}))
    return updated, deleted
