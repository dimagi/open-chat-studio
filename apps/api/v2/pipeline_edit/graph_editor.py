"""Turning a node request body into the graph edit that carries it out (#4140)."""

from typing import Any, cast

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound

from apps.api.v2.discovery.node_types import get_node_class, get_node_type_schema
from apps.pipelines.build_state import output_handles
from apps.pipelines.const import STANDARD_OUTPUT_NAME
from apps.pipelines.flow import (
    REACT_FLOW_END_TYPE,
    EdgeDiff,
    FlowEdge,
    FlowNode,
    FlowNodeData,
    NodeDiff,
    react_flow_node_type,
)
from apps.pipelines.models import Node
from apps.pipelines.nodes.base import BasePipelineNode, NodeSchema, resolve_node_class
from apps.teams.models import Team

from .facade import PipelineEdit, graph_diff
from .ids import with_free_suffix
from .node_params import node_params, writable_params
from .references import check_references

#: A node's output handles as ``{handle: branch label}``. The label is ``None`` for the single
#: standard output, and a router's branch keyword otherwise.
OutputHandles = dict[str, str | None]

#: How far right of the rightmost node a new one is parked, and the row it is parked on. The step is
#: about a node's width, so a parked node clears the one before it rather than half covering it.
PARKING_STEP_X = 400
PARKING_Y = 200


class NodeIsServerManaged(APIException):
    """The node is part of the pipeline's structure, so the API will not touch it.

    409 rather than 404: the node is there and the caller addressed it correctly, so the refusal is
    about what the node is, not about where it was looked for.
    """

    status_code = status.HTTP_409_CONFLICT


def plan_create(flow: dict, node_type: str, label: str | None, params: dict[str, Any]) -> PipelineEdit:
    """Add a node of ``node_type`` to ``flow``.

    The id is the server's to assign (W5), in the ``{type}-{5 chars}`` form the UI builder's own
    ``getNodeId`` produces, so an API-built graph is indistinguishable from a hand-built one.

    Params are already checked by the time this runs -- the type comes from the request body, so
    nothing here has to wait on the graph.
    """
    # The types `/pipeline/nodes/` serves are exactly the resolvable node classes, and
    # `get_node_type_schema` has already refused any other name, so this cannot come back None.
    node_class = cast(type[BasePipelineNode], resolve_node_class(node_type))
    node_id = _unused_node_id(flow, node_type)
    position = parking_position(flow)
    node = FlowNode(
        id=node_id,
        type=react_flow_node_type(node_type),
        position=position,
        data=FlowNodeData(
            id=node_id,
            type=node_type,
            label=label if label is not None else node_schema(node_class).label,
            params=initial_params(node_class, node_id, params),
        ),
    )
    end_nodes = _reparked_end_nodes(flow, position["x"])
    return PipelineEdit(diff=graph_diff(nodes=NodeDiff(add=[node], update=end_nodes)), node_id=node_id)


def plan_update(flow: dict, team: Team, node_id: str, label: str | None, params: dict[str, Any]) -> PipelineEdit:
    """Edit one node's params and label in place.

    Params merge key by key rather than replacing the stored dict: the point of the façade is that
    changing one setting does not mean resending the whole node.

    An edit that changes which output handles the node offers takes that node's edges with it -- see
    :func:`_rewired_edges`. Start and End are refused outright, label-only edits included.
    """
    node, content = find_node(flow, node_id)
    refuse_if_server_managed(content.type)
    before = _output_handles(content)
    if params:
        # 404s a type the API does not publish. Only when there are params to write: renaming a
        # node of such a type is not something the API has to withhold.
        node_class = get_node_class(content.type)
        params = writable_params(node_class, params)
        check_references(team, node_class, params)
        content.params = node_params(node_class, node_id, {**stored_params(content), **params})
    else:
        # Drops the resource-id mirror `to_flow_node` merged in; normalising here would write every
        # default to a row nobody asked to change.
        content.params = stored_params(content)
    if label is not None:
        content.label = label
    edges = _rewired_edges(flow, node_id, before, _output_handles(content))
    return PipelineEdit(diff=graph_diff(nodes=NodeDiff(update=[node]), edges=edges), node_id=node_id)


def plan_delete(flow: dict, node_id: str) -> PipelineEdit:
    """Remove a node, and with it every edge that named it.

    The edges go because the patch engine culls them: an edge pointing at a node that no longer
    exists breaks cycle detection and reachability outright. Start and End are refused rather than
    removed -- POST will not create them, so removing one would leave a chatbot only the UI builder
    could repair.
    """
    _node, content = find_node(flow, node_id)
    refuse_if_server_managed(content.type)
    return PipelineEdit(diff=graph_diff(nodes=NodeDiff(delete=[node_id])))


def refuse_if_server_managed(node_type: str) -> None:
    """Refuse to touch a node the server owns — Start and End, the two the API will not create.

    ``can_delete`` is the UI builder's own flag for this and is False for exactly those two, so the
    API withholds the same nodes rather than keeping a list of its own.
    """
    node_class = resolve_node_class(node_type)
    # A type naming no node class -- removed since, or never one -- has no flag to consult, and is
    # exactly the sort of node a pipeline has to be able to shed. So it is not withheld.
    if node_class is not None and not node_schema(node_class).can_delete:
        raise NodeIsServerManaged(
            f"'{node_type}' is part of the pipeline's structure: it cannot be edited or deleted through the API."
        )


def find_node(flow: dict, node_id: str) -> tuple[FlowNode, FlowNodeData]:
    """The graph's node with this id, and its content, or a 404.

    Node ids are addresses, so a wrong one is a wrong URL rather than a bad body. The content comes
    back separately because ``FlowNode.data`` is optional -- the stored blob is layout-only
    (ADR-0049) -- while a node from ``flow_data`` has had its content rebuilt from its row.
    """
    for node in flow.get("nodes", []):
        if node["id"] == node_id:
            found = FlowNode(**node)
            return found, cast(FlowNodeData, found.data)
    raise NotFound(f"This pipeline has no node '{node_id}'.")


def stored_params(content: FlowNodeData) -> dict[str, Any]:
    """The params a node's row actually holds, out of the graph's copy of them.

    ``Node.to_flow_node`` merges the resource-id mirror into *every* node type's params, so the graph
    shows a ``CodeNode`` carrying ``llm_provider_id`` and six others it does not declare. Merging
    that back would write those keys to the row on any edit, label-only ones included.
    """
    node_class = resolve_node_class(content.type)
    declared = set(node_class.model_fields) if node_class is not None else set()
    mirrored = Node.resource_param_names()
    return {name: value for name, value in content.params.items() if name in declared or name not in mirrored}


def settable_params(node: Node) -> dict[str, Any]:
    """A node row's params, narrowed to the ones a client may send back.

    The write response is documented as a valid request body, so it must not carry a param PATCH
    would refuse -- ``mcp_tools`` is stored with its default but withheld from the schema.
    """
    params = node.params or {}
    try:
        node_type_schema = get_node_type_schema(node.type)
    except NotFound:
        # A node of a type the API does not publish. Nothing about it is settable, but reporting
        # what it holds is still better than reporting nothing.
        return params
    return {name: value for name, value in params.items() if name in node_type_schema["schema"]["properties"]}


def initial_params(node_class: type[BasePipelineNode], node_id: str, supplied: dict[str, Any]) -> dict[str, Any]:
    """The params a new node starts life with: the type's defaults, then what the client sent.

    The defaults are written to the row rather than only reported -- ``update_nodes_from_data``
    stores params verbatim, so anything not written here simply is not there on the next read.
    ``name`` is required and has no default, so the server supplies the node id, as the UI builder
    does. Run through the model on the way out, the same as an edit is, so a create and a later PATCH
    of the same value store the same thing.
    """
    defaults = {
        field_name: field.get_default(call_default_factory=True)
        for field_name, field in node_class.model_fields.items()
        if not field.is_required()
    }
    return node_params(node_class, node_id, {**defaults, "name": node_id, **supplied})


def parking_position(flow: dict) -> dict:
    """Where a node with no wiring goes: clear to the right of every node already placed, bar the
    End node, which is moved out of the way instead (see :func:`_reparked_end_nodes`).

    Positions are cosmetic — they exist so a human opening the UI builder can read the graph.
    Placing a node beside the source it is wired to is a later refinement (W11); until then this
    only has to avoid stacking nodes.
    """
    placed = [node for node in flow.get("nodes", []) if node.get("type") != REACT_FLOW_END_TYPE]
    rightmost = max((node.get("position", {}).get("x") or 0 for node in placed), default=0)
    return {"x": rightmost + PARKING_STEP_X, "y": PARKING_Y}


def node_schema(node_class: type[BasePipelineNode]) -> NodeSchema:
    """A node class's ``NodeSchema``: its display label, and whether it can be added or deleted."""
    # Cast because pydantic types this config key as a plain JSON dict or a callable, while every
    # node class here stores a `NodeSchema` in it -- `deprecated_node` reads it back the same way.
    return cast(NodeSchema, node_class.model_config["json_schema_extra"])


def _unused_node_id(flow: dict, node_type: str) -> str:
    """A node id no node in this graph already has.

    A node always takes a suffix -- the type alone is not an id -- so this is :func:`.ids.with_free_suffix`
    straight, which is also where the bound on the draw and the reason for it live.
    """
    return with_free_suffix(node_type, {node["id"] for node in flow.get("nodes", [])})


def _reparked_end_nodes(flow: dict, new_node_x: float) -> list[FlowNode]:
    """The End nodes a node parked at ``new_node_x`` has caught up with, moved clear of it.

    The End node has to stay rightmost: a node level with it or past it reads as a step after the
    end. One already clear to the right is left where someone put it, and only its x ever moves.

    The move carries content because that is the only node diff ``apply_pipeline_patch`` writes
    position columns for, with params narrowed as an edit's are (see :func:`stored_params`).
    """
    moved = []
    for stored in flow.get("nodes", []):
        node = FlowNode(**stored)
        if not _is_overtaken_end_node(node, new_node_x):
            continue
        content = cast(FlowNodeData, node.data)
        content.params = stored_params(content)
        position = {"x": new_node_x + PARKING_STEP_X, "y": node.position.get("y", PARKING_Y)}
        moved.append(node.model_copy(update={"position": position}))
    return moved


def _is_overtaken_end_node(node: FlowNode, new_node_x: float) -> bool:
    """Whether ``node`` is an End node that a node parked at ``new_node_x`` has reached or passed."""
    if node.type != REACT_FLOW_END_TYPE:
        return False
    return (node.position.get("x") or 0) <= new_node_x


def _output_handles(content: FlowNodeData) -> OutputHandles:
    """A node's output handles as ``{handle: branch label}``.

    The label is what identifies a router's branch across an edit: the handle is only a position in
    ``keywords``, and positions move. A node whose type names no node class reports no handles.
    """
    return {handle["handle"]: handle["label"] for handle in output_handles(content.type, content.params, content.id)}


def _rewired_edges(flow: dict, node_id: str, before: OutputHandles, after: OutputHandles) -> EdgeDiff:
    """What an edit that changed a node's output handles does to the edges leaving it.

    Handles are positional (``output_i`` serves ``keywords[i]``), so dropping the second of three
    keywords renumbers the third rather than freeing a slot -- going by position alone would hand
    the third branch's target to the second. So old handles are matched to new ones by branch label:
    an edge follows its branch wherever it moved, and a branch that is gone takes its edge with it,
    as the UI builder's ``deleteKeyword`` does. An edge already stranded when the edit arrived stays,
    and stays reported in ``errors.edge``.
    """
    moved_to = _handle_remap(before, after)
    update: list[FlowEdge] = []
    delete: list[str] = []
    for stored in flow.get("edges", []):
        edge = FlowEdge(**stored)
        handle = edge.sourceHandle or STANDARD_OUTPUT_NAME
        if edge.source != node_id or handle not in before:
            continue
        destination = moved_to.get(handle)
        if destination is None:
            delete.append(edge.id)
        elif destination != handle:
            update.append(edge.model_copy(update={"sourceHandle": destination}))
    return EdgeDiff(update=update, delete=delete)


def _handle_remap(before: OutputHandles, after: OutputHandles) -> dict[str, str]:
    """Where each handle the node used to offer has ended up, keyed by the handle it was.

    A handle whose branch the edit removed is absent: its edge has nowhere to go. A rename counts as
    a removal, since inheriting the old branch's target would wire the new one somewhere nobody chose.
    """
    if _labels_are_distinct(before) and _labels_are_distinct(after):
        destinations = {label: handle for handle, label in after.items()}
        return {handle: destinations[label] for handle, label in before.items() if label in destinations}
    # Duplicate branch labels: keywords have to be unique, but a router that breaks that is still
    # writable, and which edge belongs to which of two identical branches is a guess. So handles are
    # followed by position instead, and only an edge left with no handle at all is dropped.
    return {handle: handle for handle in before if handle in after}


def _labels_are_distinct(handles: OutputHandles) -> bool:
    """Whether every handle in the map carries a different branch label."""
    return len(set(handles.values())) == len(handles)
