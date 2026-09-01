"""Turning an edge request body into the graph edit that carries it out (#4141).

The endpoints refuse a wire the graph could not carry -- an endpoint that is not a node here, a
handle the node does not offer, a pair already wired -- and let through one that merely leaves the
pipeline unfinished. A cycle or an unreachable End node is a property of the *graph* rather than of
this edge, so it persists and comes back in the errors report for the agent to repair.
"""

from typing import cast

from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.settings import api_settings

from apps.pipelines.build_state import input_handles, output_handles
from apps.pipelines.const import STANDARD_INPUT_NAME, STANDARD_OUTPUT_NAME
from apps.pipelines.flow import EdgeDiff, FlowEdge, FlowNode, FlowNodeData
from apps.pipelines.nodes.base import PipelineRouterNode, resolve_node_class

from .facade import PipelineEdit, graph_diff
from .ids import with_free_suffix

#: The four values that identify an edge: ``(source, source handle, target, target handle)``. Two
#: edges sharing all four are the same wire, whatever ids they carry.
Wiring = tuple[str, str, str, str]


def plan_create(
    flow: dict, source: str, target: str, source_handle: str | None, target_handle: str | None
) -> PipelineEdit:
    """Wire ``source`` to ``target``.

    The id is the server's to assign (W5), built with react-flow's own ``getEdgeId`` formula so that
    an API-wired edge reads like a hand-wired one. Not byte-identical to one, though: the UI builder
    renders its target handles without ids, so its edges carry ``targetHandle: null`` and their ids end
    at the target's node id, where these end in ``input`` (see :func:`_wiring_of`). Ids are opaque --
    nothing parses one -- and the duplicate check compares the wiring rather than the id, so the two
    conventions sit side by side in one graph.

    Neither node is moved: Phase 1 leaves the canvas alone (W11), so a node keeps the position it was
    parked at and wiring cannot shuffle a layout someone arranged in the UI builder.
    """
    source_node, target_node = _endpoints(flow, source, target)
    from_handle = _source_handle(source_node, source_handle)
    to_handle = _target_handle(target_node, target_handle)
    wiring: Wiring = (source, from_handle, target, to_handle)
    _refuse_duplicate(flow, wiring)
    edge = FlowEdge(
        id=_unused_edge_id(flow, wiring),
        source=source,
        target=target,
        sourceHandle=from_handle,
        targetHandle=to_handle,
    )
    return PipelineEdit(diff=graph_diff(edges=EdgeDiff(add=[edge])), edge=edge)


def plan_delete(flow: dict, edge_id: str) -> PipelineEdit:
    """Remove one edge, leaving both nodes it joined exactly where they are.

    Edge ids are addresses, so a wrong one is a wrong URL rather than a bad body -- and a *repeated*
    delete gets that same 404, which is what makes an unwire safe to retry: the second call reports the
    edge already gone rather than disturbing the graph the first call left.
    """
    if edge_id not in {edge["id"] for edge in flow.get("edges", [])}:
        raise NotFound(f"This pipeline has no edge '{edge_id}'.")
    return PipelineEdit(diff=graph_diff(edges=EdgeDiff(delete=[edge_id])))


def _refuse_duplicate(flow: dict, wiring: Wiring) -> None:
    """Refuse a wire the graph already holds, naming the edge that holds it.

    Two edges wiring the same pair from the same handle are one edge said twice: the pipeline follows
    the wire once either way, so the second is invisible except as something an agent has to clean up.
    Refusing it is what makes the most retry-prone write in the API safe to retry (spec §8.2) -- a
    repeat leaves the graph exactly as the first call left it -- and the existing edge's id is in the
    refusal so a client that never saw the first response can carry on without re-reading the graph.
    """
    for stored in flow.get("edges", []):
        edge = FlowEdge(**stored)
        if _wiring_of(edge) == wiring:
            raise serializers.ValidationError(
                {
                    api_settings.NON_FIELD_ERRORS_KEY: [
                        f"These nodes are already wired this way, by edge '{edge.id}'. Nothing was changed."
                    ]
                }
            )


def _endpoints(flow: dict, source: str, target: str) -> tuple[FlowNodeData, FlowNodeData]:
    """The content of the two nodes the edge names, or a 400 naming every field at fault.

    400 rather than the 404 a wrong node id in a *path* gets: an endpoint is a field of the body, so
    naming a node this pipeline does not have is a bad body rather than a bad address. Node ids are
    unique per pipeline rather than globally, so an id from another chatbot's graph lands here too --
    and has to, since an edge naming a node the graph cannot resolve breaks cycle detection and
    reachability outright.

    Both are reported at once rather than one per call, since a client working from a stale read of
    the graph is as likely to have both wrong as one.
    """
    nodes = {node["id"]: node for node in flow.get("nodes", [])}
    missing = {
        field: f"This pipeline has no node '{node_id}'."
        for field, node_id in (("source", source), ("target", target))
        if node_id not in nodes
    }
    if missing:
        raise serializers.ValidationError(missing)
    # `flow_data` rebuilds every node's content from its row, so `data` is always populated.
    return (
        cast(FlowNodeData, FlowNode(**nodes[source]).data),
        cast(FlowNodeData, FlowNode(**nodes[target]).data),
    )


def _source_handle(content: FlowNodeData, requested: str | None) -> str:
    """Which of the source node's output handles the edge leaves from.

    An omitted handle is filled in only when the node offers exactly one. A router offers one per
    branch, and picking a branch nobody named would route traffic somewhere nobody chose -- so the
    refusal lists the handles on offer, which is what a node write and ``unwired_handles`` publish.
    """
    offered = [handle["handle"] for handle in output_handles(content.type, content.params, content.id)]
    if not offered:
        raise serializers.ValidationError({"source": _why_no_output_handles(content)})
    if requested is None:
        if len(offered) > 1:
            raise serializers.ValidationError(
                {
                    "source_handle": (
                        f"'{content.id}' offers more than one output handle; name the one to wire "
                        f"from: {', '.join(offered)}."
                    )
                }
            )
        return offered[0]
    if requested not in offered:
        raise serializers.ValidationError(
            {
                "source_handle": (
                    f"'{requested}' is not an output handle '{content.id}' offers. On offer: {', '.join(offered)}."
                )
            }
        )
    return requested


def _why_no_output_handles(content: FlowNodeData) -> str:
    """Why this node offers no output handle. Three different situations, three different answers.

    Only one of them is the caller's to fix, and it is the one reached down the documented happy path:
    ``POST /pipeline/nodes/ {"type": "RouterNode"}`` stores ``keywords: []``, and a router's handles
    *are* its keywords, so the one node type ``source_handle`` exists for arrives unwirable. Handed the
    End node's answer, an agent reads "no edge can leave it" as "this node can never be a source" and
    deletes it, when the fix is one PATCH away -- so the message has to name that PATCH.
    """
    node_class = resolve_node_class(content.type)
    if node_class is None:
        # A type naming no node class -- removed since, or never one. Nothing the caller sends makes
        # this node wirable; moving the pipeline off the type is the only way forward.
        return (
            f"'{content.id}' is of type '{content.type}', which this API does not publish, so its "
            f"output handles cannot be determined and no edge can leave it."
        )
    if issubclass(node_class, PipelineRouterNode):
        return (
            f"'{content.id}' has no output handles yet: a router's handles are its branches. Set its "
            f"`keywords` (PATCH the node), then wire the branch you want."
        )
    # The End node, the one type that genuinely offers none: nothing runs after the end of the pipeline.
    return f"'{content.id}' offers no output handles, so no edge can leave it."


def _target_handle(content: FlowNodeData, requested: str | None) -> str:
    """Which of the target node's input handles the edge points at.

    Never required: every node type has exactly one, implicit, input handle -- bar Start, which has
    none. The field exists all the same so an edge read back from ``GET /inspect/`` can be sent
    straight back, and so a multi-input node type would not need a new field.
    """
    accepted = input_handles(content.type)
    if not accepted:
        raise serializers.ValidationError(
            {"target": f"'{content.id}' has no input handle, so no edge can point at it."}
        )
    if requested is None:
        return accepted[0]
    if requested not in accepted:
        raise serializers.ValidationError(
            {
                "target_handle": (
                    f"'{requested}' is not an input handle '{content.id}' accepts. Accepted: {', '.join(accepted)}."
                )
            }
        )
    return requested


def _wiring_of(edge: FlowEdge) -> Wiring:
    """An edge's four identifying values, with an absent handle read as the node's only one.

    The two sides go absent for different reasons, and both have to normalise or a duplicate slips in
    beside the edge it duplicates:

    * ``targetHandle`` because the UI builder renders its target handles with no id at all
      (``NodeInput.tsx``, ``BoundaryNode.tsx``), so *every* edge it draws stores a null one.
    * ``sourceHandle`` because it is optional in the stored shape rather than unnamed in the builder --
      which does name its source handles (``output``, or ``output_N`` on a router). An omitted or null
      one means the standard output, exactly as ``FlowEdge``'s own default declares and as
      ``graph.Edge.is_conditional`` and ``unwired_handles`` both read it; the pipeline factory's own
      seed edge and any imported graph are where they turn up.
    """
    return (
        edge.source,
        edge.sourceHandle or STANDARD_OUTPUT_NAME,
        edge.target,
        edge.targetHandle or STANDARD_INPUT_NAME,
    )


def _unused_edge_id(flow: dict, wiring: Wiring) -> str:
    """An edge id no edge in this graph already has.

    The base is what react-flow's own ``getEdgeId`` formula draws from these four values, which keeps
    an API-wired edge legible beside a hand-wired one (though not identical to it -- see
    :func:`plan_create`). It is not unique on its own, though: an edit that moves a router's keywords
    leaves an edge holding the id of a wiring it no longer has
    (see :func:`~apps.api.v2.pipeline_edit.graph_editor._rewired_edges`), and the patch engine treats
    an add whose id already exists as a no-op -- which would answer 201 having stored nothing. So a
    taken id gets a suffix. Trying the bare base first is the whole of what differs from how a node id
    is drawn; the draw itself, and the bound on it, are :func:`.ids.with_free_suffix`.
    """
    source, source_handle, target, target_handle = wiring
    base = f"reactflow__edge-{source}{source_handle}-{target}{target_handle}"
    taken = {edge["id"] for edge in flow.get("edges", [])}
    return base if base not in taken else with_free_suffix(base, taken)
