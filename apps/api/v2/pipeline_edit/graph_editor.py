"""Turning a node request body into the graph edit that carries it out (#4140)."""

from typing import Any, cast
from uuid import uuid4

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound

from apps.api.v2.discovery.node_types import get_node_class, get_node_type_schema
from apps.pipelines.build_state import output_handles
from apps.pipelines.const import STANDARD_OUTPUT_NAME
from apps.pipelines.flow import (
    EdgeDiff,
    FlowEdge,
    FlowNode,
    FlowNodeData,
    NodeDiff,
    PipelineDiffPayload,
    react_flow_node_type,
)
from apps.pipelines.models import Node
from apps.pipelines.nodes.base import BasePipelineNode, NodeSchema, resolve_node_class

from .facade import PipelineEdit
from .node_params import node_params, writable_params
from .references import check_references

#: A node's output handles as ``{handle: branch label}``. The label is ``None`` for the single
#: standard output, and a router's branch keyword otherwise.
OutputHandles = dict[str, str | None]

#: How far right of the rightmost node a new one is parked, and the row it is parked on.
PARKING_STEP_X = 200
PARKING_Y = 200

#: The id suffix the builder's own ``getNodeId`` produces, and how many draws are spent trying to
#: find a free one before falling back to a full-length uuid.
SHORT_ID_LENGTH = 5
ID_ATTEMPTS = 5

#: ``PipelineDiffPayload`` requires this, but ``apply_pipeline_patch`` never reads it: it is the
#: builder's optimistic-concurrency token, and the façade holds a row lock instead (W7), so there is
#: no revision for it to check against.
UNUSED_BASE_REVISION = 0


class NodeIsServerManaged(APIException):
    """The node is part of the pipeline's structure, so the API will not touch it.

    409 rather than 404: the node is there and the caller addressed it correctly, so the refusal is
    about what the node is, not about where it was looked for.
    """

    status_code = status.HTTP_409_CONFLICT


def plan_create(flow: dict, node_type: str, label: str | None, params: dict[str, Any]) -> PipelineEdit:
    """Add a node of ``node_type`` to ``flow``.

    The id is the server's to assign (W5), in the ``{type}-{5 chars}`` form the builder's own
    ``getNodeId`` produces, so an API-built graph is indistinguishable from a hand-built one.

    Params are already checked by the time this runs: the type is known from the request body, so
    nothing here has to wait on the graph.
    """
    # The types `/pipeline/nodes/` serves are exactly the resolvable node classes, and
    # `get_node_type_schema` has already refused any other name, so this cannot come back None.
    node_class = cast(type[BasePipelineNode], resolve_node_class(node_type))
    node_id = _unused_node_id(flow, node_type)
    node = FlowNode(
        id=node_id,
        type=react_flow_node_type(node_type),
        position=parking_position(flow),
        data=FlowNodeData(
            id=node_id,
            type=node_type,
            label=label if label is not None else node_schema(node_class).label,
            params=initial_params(node_class, node_id, params),
        ),
    )
    return PipelineEdit(diff=_diff(NodeDiff(add=[node])), node_id=node_id)


def plan_update(flow: dict, options: dict, node_id: str, label: str | None, params: dict[str, Any]) -> PipelineEdit:
    """Edit one node's params and label in place.

    Params merge key by key rather than replacing the stored dict: the point of the façade is that
    changing one setting does not mean resending the whole node.

    An edit that changes which output handles the node offers takes that node's edges with it: a
    dropped router keyword drops the edge that served it, and a keyword that only moved carries its
    edge along. See :func:`_rewired_edges` -- there is no edge endpoint an agent could use to clear
    up after itself, so leaving the wreckage would only strand it.

    Start and End are refused outright, whichever half of the body is sent: a label-only edit to
    one is as refused as a change to its params.
    """
    node, content = find_node(flow, node_id)
    refuse_if_server_managed(content.type)
    before = _output_handles(content)
    if params:
        # 404s a type the API does not publish. Only when there are params to write: renaming a
        # node of such a type is not something the API has to withhold.
        node_class = get_node_class(content.type)
        params = writable_params(node_class, params)
        check_references(options, node_class, params)
        # Merge first: the model is all-or-nothing, so it must see the whole node.
        content.params = node_params(node_class, node_id, {**stored_params(content), **params})
    else:
        # Drops the resource-id mirror `to_flow_node` merged in; normalising here would write every
        # default to a row nobody asked to change.
        content.params = stored_params(content)
    if label is not None:
        content.label = label
    edges = _rewired_edges(flow, node_id, before, _output_handles(content))
    return PipelineEdit(diff=_diff(NodeDiff(update=[node]), edges), node_id=node_id)


def plan_delete(flow: dict, node_id: str) -> PipelineEdit:
    """Remove a node, and with it every edge that named it.

    The edges go because the patch engine culls them, not because the caller asked: an edge left
    pointing at a node that no longer exists breaks cycle detection and reachability outright.

    Start and End are refused rather than removed, the same way an edit to one is: they are the
    types POST will not create, so removing one would leave a chatbot only the builder could repair.
    """
    _node, content = find_node(flow, node_id)
    refuse_if_server_managed(content.type)
    return PipelineEdit(diff=_diff(NodeDiff(delete=[node_id])))


def refuse_if_server_managed(node_type: str) -> None:
    """Refuse to touch a node the server owns — Start and End, the two the API will not create.

    ``can_delete`` is the builder's own flag for this and is False for exactly those two, so the API
    withholds the same nodes the builder does rather than keeping a list of its own.
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

    Node ids are addresses, so a wrong one is a wrong URL rather than a bad body. The content is
    handed back separately because ``FlowNode.data`` is optional -- the stored blob is layout-only
    (ADR-0049) -- while a node from ``flow_data`` has always had its content rebuilt from its row.
    """
    for node in flow.get("nodes", []):
        if node["id"] == node_id:
            found = FlowNode(**node)
            return found, cast(FlowNodeData, found.data)
    raise NotFound(f"This pipeline has no node '{node_id}'.")


def stored_params(content: FlowNodeData) -> dict[str, Any]:
    """The params a node's row actually holds, out of the graph's copy of them.

    ``Node.to_flow_node`` merges the resource-id mirror into *every* node type's params, so the
    graph shows a ``CodeNode`` carrying ``llm_provider_id`` and six others it does not declare.
    Merging that back would write those keys to the row on any edit, including a label-only one --
    and the write's own response would then be a body the caller cannot send back, because PATCH
    refuses a param the type does not declare.
    """
    node_class = resolve_node_class(content.type)
    declared = set(node_class.model_fields) if node_class is not None else set()
    mirrored = Node.resource_param_names()
    return {name: value for name, value in content.params.items() if name in declared or name not in mirrored}


def settable_params(node: Node) -> dict[str, Any]:
    """A node row's params, narrowed to the ones a client may send back.

    The write response is documented as a valid request body, so it must not carry a param that
    PATCH would refuse -- ``mcp_tools`` is stored with its default but withheld from the schema.
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

    The defaults are written to the row rather than only reported, so the node reads back through
    ``/inspect/`` as the same thing the create response described — ``update_nodes_from_data`` stores
    params verbatim, so anything not written here simply is not there.

    ``name`` is required and has no default, so the server supplies one: the node id, which is what
    the builder writes when a node is dragged onto the canvas.

    Run through the model on the way out, the same as an edit is: a create and a later PATCH that
    sends the same value should not store two different things.
    """
    defaults = {
        field_name: field.get_default(call_default_factory=True)
        for field_name, field in node_class.model_fields.items()
        if not field.is_required()
    }
    return node_params(node_class, node_id, {**defaults, "name": node_id, **supplied})


def parking_position(flow: dict) -> dict:
    """Where a node with no wiring goes: clear to the right of every node already placed.

    Positions are cosmetic — they exist so a human opening the builder can read the graph, and
    nothing about execution or validity depends on them. Placing a node beside the source it is
    wired to is a later refinement (W11); until then this only has to avoid stacking nodes.
    """
    rightmost = max((node.get("position", {}).get("x") or 0 for node in flow.get("nodes", [])), default=0)
    return {"x": rightmost + PARKING_STEP_X, "y": PARKING_Y}


def node_schema(node_class: type[BasePipelineNode]) -> NodeSchema:
    """A node class's ``NodeSchema``: its display label, and whether it can be added or deleted."""
    # Cast because pydantic types this config key as a plain JSON dict or a callable, while every
    # node class here stores a `NodeSchema` in it -- `deprecated_node` reads it back the same way.
    return cast(NodeSchema, node_class.model_config["json_schema_extra"])


def _unused_node_id(flow: dict, node_type: str) -> str:
    """A node id no node in this graph already has.

    Five hex characters is about a million ids, which is plenty per pipeline but not so many that a
    collision can be waved away: ``apply_pipeline_patch`` treats an add whose id already exists as a
    no-op, so a clash would answer 201 while describing the node that was already there.

    Bounded rather than looping until it wins: the retries are what make a collision a non-event,
    and the full-length fallback is what stops an exhausted or degenerate id source from hanging
    the request instead.
    """
    taken = {node["id"] for node in flow.get("nodes", [])}
    for _attempt in range(ID_ATTEMPTS):
        candidate = f"{node_type}-{uuid4().hex[:SHORT_ID_LENGTH]}"
        if candidate not in taken:
            return candidate
    return f"{node_type}-{uuid4().hex}"


def _output_handles(content: FlowNodeData) -> OutputHandles:
    """A node's output handles as ``{handle: branch label}``.

    The label is what identifies a router's branch across an edit: the handle is only a position in
    ``keywords``, and positions move. A node whose type names no node class reports no handles, so
    nothing about its wiring is inferred from an edit either.
    """
    return {handle["handle"]: handle["label"] for handle in output_handles(content.type, content.params, content.id)}


def _rewired_edges(flow: dict, node_id: str, before: OutputHandles, after: OutputHandles) -> EdgeDiff:
    """What an edit that changed a node's output handles does to the edges leaving it.

    Handles are positional (``output_i`` serves ``keywords[i]``), so dropping the second of three
    keywords renumbers the third rather than freeing a slot. Going by position alone would delete
    the third branch's edge and quietly hand its target to the second, so old handles are matched to
    new ones by branch label instead: an edge follows its branch wherever it moved, and a branch
    that is gone takes its edge with it -- which is what the builder's own ``deleteKeyword`` does.

    Only the handles this edit removed are acted on. An edge already stranded when the edit arrived
    -- left by an import, or a builder session -- stays, and stays reported in ``errors.edge``.
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

    A handle whose branch the edit removed is absent from the result: its edge has nowhere to go.
    A rename counts as a removal, because nothing in the body says otherwise -- the old branch is
    not in the new list, and inheriting its target would wire the new one somewhere nobody chose.
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


def _diff(nodes: NodeDiff, edges: EdgeDiff | None = None) -> PipelineDiffPayload:
    """One node change, and whatever it does to that node's edges, in the shape the patch engine takes."""
    return PipelineDiffPayload(base_revision=UNUSED_BASE_REVISION, nodes=nodes, edges=edges or EdgeDiff())
