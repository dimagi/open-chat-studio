"""Turning a node request body into the graph edit that carries it out (#4140)."""

from functools import cache
from typing import cast
from uuid import uuid4

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.api.v2.discovery.node_types import get_node_type, reference_param_names
from apps.pipelines.flow import FlowNode, FlowNodeData, NodeDiff, PipelineDiffPayload, react_flow_node_type
from apps.pipelines.models import Node
from apps.pipelines.nodes.base import NodeSchema, resolve_node_class

from .facade import PipelineEdit
from .param_types import param_type_errors
from .references import check_references

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


def served_type_for_body(node_type: str) -> dict:
    """The named node type, or a 400 -- the name came out of a request body, not out of the URL.

    ``get_node_type`` answers 404 because its own caller was addressed by path
    (``/pipeline/nodes/{type}/``), where an unknown name really is a wrong URL. Here the same name
    is a field the client chose, so it is reported the way every other bad field is, and an agent
    does not have to tell "no such chatbot" apart from "no such node type" by reading prose.
    """
    try:
        return get_node_type(node_type)
    except NotFound as unknown:
        raise ValidationError({"type": unknown.detail}) from unknown


def warm_option_lists(options, params: dict) -> None:
    """Build the team's option lists before the pipeline row is locked, if this body needs them.

    ``options_for_team`` is around fifteen queries and parses every custom action's OpenAPI schema.
    The reference check that consumes it runs inside the lock -- it needs the node's type, and for
    a PATCH that comes from the graph -- so the build is done here, ahead of the transaction, and
    the memoised accessor hands the result over for free once the lock is held.

    Only for a body that names something referenceable: a write touching no reference must not pay
    for the lists at all.
    """
    if params and not reference_param_names().isdisjoint(params):
        options()


def check_params(served_type: dict, options, params: dict) -> None:
    """Everything a param has to satisfy before it is allowed anywhere near the graph.

    Order matters: a name has to be recognised before its type means anything, and a type has to
    hold before a value can be read as one reference or as a list of them.
    """
    check_param_names(served_type, params)
    check_param_types(served_type, params)
    check_references(options, served_type["type"], served_type["schema"]["properties"], params)


def plan_create(flow: dict, served_type: dict, label: str | None, params: dict) -> PipelineEdit:
    """Add a node of ``served_type`` to ``flow``.

    The id is the server's to assign (W5), in the ``{type}-{5 chars}`` form the builder's own
    ``getNodeId`` produces, so an API-built graph is indistinguishable from a hand-built one.

    Params are already checked by the time this runs: the type is known from the request body, so
    nothing here has to wait on the graph.
    """
    node_type = served_type["type"]
    # The types `/pipeline/nodes/` serves are exactly the resolvable node classes, and
    # `served_type_for_body` has already refused any other name, so this cannot come back None.
    node_class = cast(type, resolve_node_class(node_type))
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


def plan_update(flow: dict, options, node_id: str, label: str | None, params: dict) -> PipelineEdit:
    """Edit one node's params and label in place.

    Params merge key by key rather than replacing the stored dict: the point of the façade is that
    changing one setting does not mean resending the whole node. Nothing is pruned or re-indexed as
    a side effect either — an edit that leaves an edge stranded reports it (``errors.edge``) and
    leaves the edge alone, because which one to drop is the agent's call, not ours.

    Start and End are refused outright, whichever half of the body is sent: a label-only edit to
    one is as refused as a change to its params.
    """
    node, content = find_node(flow, node_id)
    refuse_if_server_managed(content.type)
    if params:
        # 404s a type the API does not publish at all -- a deprecated one, say, whose params it
        # cannot describe and so cannot check. Only when there are params to check: renaming a node
        # of such a type is not something the API has to withhold.
        check_params(get_node_type(content.type), options, params)

    content.params = {**stored_params(content), **params}
    if label is not None:
        content.label = label
    return PipelineEdit(diff=_diff(NodeDiff(update=[node])), node_id=node_id)


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


def check_param_names(served_type: dict, params: dict) -> None:
    """Refuse a param the node type does not declare.

    Node models ignore unknown fields, so an unrecognised param would be stored, never read, and the
    write would look like it had taken effect. The names checked against are the ones
    ``/pipeline/nodes/`` published, so a param the API withholds is not settable either.
    """
    unknown = sorted(set(params) - set(served_type["schema"]["properties"]))
    if unknown:
        message = (
            f"'{served_type['type']}' declares no such param. See GET /api/v2/pipeline/nodes/{served_type['type']}/."
        )
        raise ValidationError({"params": dict.fromkeys(unknown, message)})


def check_param_types(served_type: dict, params: dict) -> None:
    """Refuse a param whose value is the wrong shape for what the type declares.

    Unlike a missing or semantically wrong param, this is not reported and kept: see
    ``param_types`` for why a value the node cannot parse has to be turned away at the door.
    """
    if errors := param_type_errors(served_type["schema"]["properties"], params):
        raise ValidationError({"params": errors})


def stored_params(content: FlowNodeData) -> dict:
    """The params a node's row actually holds, out of the graph's copy of them.

    ``Node.to_flow_node`` merges the resource-id mirror into *every* node type's params, so the
    graph shows a ``CodeNode`` carrying ``llm_provider_id`` and six others it does not declare.
    Merging that back would write those keys to the row on any edit, including a label-only one --
    and the write's own response would then be a body the caller cannot send back, because PATCH
    refuses a param the type does not declare.
    """
    node_class = resolve_node_class(content.type)
    declared = set(node_class.model_fields) if node_class is not None else set()
    return {
        name: value for name, value in content.params.items() if name in declared or name not in _resource_mirror_keys()
    }


def settable_params(node) -> dict:
    """A node row's params, narrowed to the ones a client may send back.

    The write response is documented as a valid request body, so it must not carry a param that
    PATCH would refuse -- ``mcp_tools`` is stored with its default but withheld from the schema.
    """
    params = node.params or {}
    try:
        served = get_node_type(node.type)
    except NotFound:
        # A node of a type the API does not publish. Nothing about it is settable, but reporting
        # what it holds is still better than reporting nothing.
        return params
    return {name: value for name, value in params.items() if name in served["schema"]["properties"]}


def initial_params(node_class: type, node_id: str, supplied: dict) -> dict:
    """The params a new node starts life with: the type's defaults, then what the client sent.

    The defaults are written to the row rather than only reported, so the node reads back through
    ``/inspect/`` as the same thing the create response described — ``update_nodes_from_data`` stores
    params verbatim, so anything not written here simply is not there.

    ``name`` is required and has no default, so the server supplies one: the node id, which is what
    the builder writes when a node is dragged onto the canvas.
    """
    defaults = {
        field_name: field.get_default(call_default_factory=True)
        for field_name, field in node_class.model_fields.items()
        if not field.is_required()
    }
    return {**defaults, "name": node_id, **supplied}


def parking_position(flow: dict) -> dict:
    """Where a node with no wiring goes: clear to the right of every node already placed.

    Positions are cosmetic — they exist so a human opening the builder can read the graph, and
    nothing about execution or validity depends on them. Placing a node beside the source it is
    wired to is a later refinement (W11); until then this only has to avoid stacking nodes.
    """
    rightmost = max((node.get("position", {}).get("x") or 0 for node in flow.get("nodes", [])), default=0)
    return {"x": rightmost + PARKING_STEP_X, "y": PARKING_Y}


def node_schema(node_class: type) -> NodeSchema:
    """A node class's ``NodeSchema``: its display label, and whether it can be added or deleted."""
    return node_class.model_config["json_schema_extra"]


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


@cache
def _resource_mirror_keys() -> frozenset[str]:
    """The param names ``Node.resource_params`` merges into every node's params, whatever its type."""
    return frozenset({f"{field}_id" for field in Node.resource_fk_fields()} | {"collection_index_ids"})


def _diff(nodes: NodeDiff) -> PipelineDiffPayload:
    """One node change, in the shape the builder's patch engine takes."""
    return PipelineDiffPayload(base_revision=UNUSED_BASE_REVISION, nodes=nodes)
