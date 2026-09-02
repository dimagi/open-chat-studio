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

from apps.pipelines.build_state import NoOutputHandles, input_handles, output_handles, why_no_output_handles
from apps.pipelines.flow import EdgeDiff, Flow, FlowEdge, FlowNodeData, react_flow_edge_id

from .facade import PipelineEdit, graph_diff
from .ids import with_free_suffix

#: The four values that identify an edge: ``(source, source handle, target, target handle)``, as
#: :attr:`~apps.pipelines.flow.FlowEdge.wiring` reads them off a stored edge.
Wiring = tuple[str, str, str, str]


def plan_create(
    flow: Flow, source: str, target: str, source_handle: str | None, target_handle: str | None
) -> PipelineEdit:
    """Wire ``source`` to ``target``.

    The id is the server's to assign (W5), built with react-flow's own ``getEdgeId`` formula so that
    an API-wired edge reads like a hand-wired one. Not byte-identical to one, though: the UI builder
    renders its target handles without ids, so its edges carry ``targetHandle: null`` and their ids end
    at the target's node id, where these end in ``input`` (see
    :attr:`~apps.pipelines.flow.FlowEdge.target_handle_name`). Ids are opaque --
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
    return PipelineEdit(diff=graph_diff(edges=EdgeDiff(add=[edge])), written_id=edge.id)


def plan_delete(flow: Flow, edge_id: str) -> PipelineEdit:
    """Remove one edge, leaving both nodes it joined exactly where they are.

    Edge ids are addresses, so a wrong one is a wrong URL rather than a bad body -- and a *repeated*
    delete gets that same 404, which is what makes an unwire safe to retry: the second call reports the
    edge already gone rather than disturbing the graph the first call left.
    """
    if edge_id not in {edge.id for edge in flow.edges}:
        raise NotFound(f"This pipeline has no edge '{edge_id}'.")
    return PipelineEdit(diff=graph_diff(edges=EdgeDiff(delete=[edge_id])))


def _refuse_duplicate(flow: Flow, wiring: Wiring) -> None:
    """Refuse a wire the graph already holds, naming the edge that holds it.

    Two edges wiring the same pair from the same handle are one edge said twice: the pipeline follows
    the wire once either way, so the second is invisible except as something an agent has to clean up.
    Refusing it is what makes the most retry-prone write in the API safe to retry (spec §8.2) -- a
    repeat leaves the graph exactly as the first call left it -- and the existing edge's id is in the
    refusal so a client that never saw the first response can carry on without re-reading the graph.
    """
    for edge in flow.edges:
        if edge.wiring == wiring:
            raise serializers.ValidationError(
                {
                    api_settings.NON_FIELD_ERRORS_KEY: [
                        f"These nodes are already wired this way, by edge '{edge.id}'. Nothing was changed."
                    ]
                }
            )


def _endpoints(flow: Flow, source: str, target: str) -> tuple[FlowNodeData, FlowNodeData]:
    """The content of the two nodes the edge names, or a 400 naming every field at fault.

    400 rather than the 404 a wrong node id in a *path* gets: an endpoint is a field of the body, so
    naming a node this pipeline does not have is a bad body rather than a bad address. Node ids are
    unique per pipeline rather than globally, so an id from another chatbot's graph lands here too --
    and has to, since an edge naming a node the graph cannot resolve breaks cycle detection and
    reachability outright.

    Both are reported at once rather than one per call, since a client working from a stale read of
    the graph is as likely to have both wrong as one.
    """
    nodes = {node.id: node for node in flow.nodes}
    missing = {
        field: f"This pipeline has no node '{node_id}'."
        for field, node_id in (("source", source), ("target", target))
        if node_id not in nodes
    }
    if missing:
        raise serializers.ValidationError(missing)
    # `flow_data` rebuilds every node's content from its row, so `data` is always populated.
    return (
        cast(FlowNodeData, nodes[source].data),
        cast(FlowNodeData, nodes[target].data),
    )


def _source_handle(content: FlowNodeData, requested: str | None) -> str:
    """Which of the source node's output handles the edge leaves from.

    An omitted handle is filled in only when the node offers exactly one. A router offers one per
    branch, and picking a branch nobody named would route traffic somewhere nobody chose -- so the
    refusal lists the handles on offer, which is what a node write and ``unwired_handles`` publish.
    """
    offered = [handle["handle"] for handle in output_handles(content.type, content.params, content.id)]
    if not offered:
        raise serializers.ValidationError({"source": _no_output_handles_message(content)})
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


def _no_output_handles_message(content: FlowNodeData) -> str:
    """What to tell a caller that tried to wire from a node offering no output handle.

    Only one of the cases is the caller's to fix, and it is the one reached straight down the
    documented happy path: ``POST /pipeline/nodes/ {"type": "RouterNode"}`` stores ``keywords: []``,
    and a router's handles *are* its keywords, so the one node type ``source_handle`` exists for
    arrives unwirable. Handed the End node's answer, an agent reads "no edge can leave it" as "this
    node can never be a source" and deletes it, when the fix is one PATCH away.

    Which case it is comes from :func:`~apps.pipelines.build_state.why_no_output_handles`, so this
    holds only the wording -- and holds it as a lookup, so a case added there raises ``KeyError`` here
    instead of quietly inheriting the End node's answer.
    """
    return _NO_OUTPUT_HANDLES_MESSAGES[why_no_output_handles(content.type)].format(id=content.id, type=content.type)


#: Wording per :class:`~apps.pipelines.build_state.NoOutputHandles` case. A mapping rather than a
#: chain of branches: every member has to be named, so adding one to the enum fails loudly here.
_NO_OUTPUT_HANDLES_MESSAGES = {
    # Not the same as a type the API merely withholds: `BooleanNode` resolves and offers two handles,
    # so it never lands here. Nothing the caller sends makes such a node wirable.
    NoOutputHandles.UNKNOWN_TYPE: (
        "'{id}' is of type '{type}', which names no node type this server knows, so its output "
        "handles cannot be determined and no edge can leave it."
    ),
    NoOutputHandles.NO_BRANCHES: (
        "'{id}' has no output handles yet: a router's handles are its branches. Set its `keywords` "
        "(PATCH the node), then wire the branch you want."
    ),
    NoOutputHandles.TERMINAL: "'{id}' offers no output handles, so no edge can leave it.",
    NoOutputHandles.UNDETERMINED: (
        "'{id}' offers no output handles, so no edge can leave it. Why is not something this server "
        "can say for a '{type}' node."
    ),
}


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


def _unused_edge_id(flow: Flow, wiring: Wiring) -> str:
    """An edge id no edge in this graph already has.

    The base is :func:`~apps.pipelines.flow.react_flow_edge_id` over these four values, which keeps an
    API-wired edge legible beside a hand-wired one (though not identical to it -- see
    :func:`plan_create`). It is not unique on its own, though: an edit that moves a router's keywords
    leaves an edge holding the id of a wiring it no longer has
    (see :func:`~apps.api.v2.pipeline_edit.graph_editor._rewired_edges`), and the patch engine treats
    an add whose id already exists as a no-op -- which would answer 201 having stored nothing. So a
    taken id gets a suffix. Trying the bare base first is the whole of what differs from how a node id
    is drawn; the draw itself, and the bound on it, are :func:`.ids.with_free_suffix`.
    """
    base = react_flow_edge_id(*wiring)
    taken = {edge.id for edge in flow.edges}
    return base if base not in taken else with_free_suffix(base, taken)
