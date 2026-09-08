"""Turning an edge request body into the graph edit that carries it out (#4141).

The endpoints refuse a wire the graph could not carry -- an endpoint that is not a node here, a
handle the node does not offer, a pair already wired -- and let through one that merely leaves the
pipeline unfinished: a cycle or an unreachable End node is a property of the *graph* rather than of
this edge, so it persists and comes back in the errors report.

A wire call carries as many wires as the body's ``max_length`` allows, and is all or nothing: one
refused wire refuses the call, and the graph is left as the call found it.
"""

from typing import ClassVar, cast

from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.settings import api_settings

from apps.pipelines.build_state import NoOutputHandles, input_handles, output_handles, why_no_output_handles
from apps.pipelines.flow import EdgeDiff, Flow, FlowEdge, FlowNodeData

from .facade import PipelineEdit, graph_diff
from .ids import with_free_suffix
from .serializers import WIRES_FIELD

#: The four values that identify an edge: ``(source, source handle, target, target handle)``, as
#: :attr:`~apps.pipelines.flow.FlowEdge.wiring` reads them off a stored edge.
Wiring = tuple[str, str, str, str]


def plan_create(flow: Flow, wires: list[dict]) -> PipelineEdit:
    """Wire every pair ``wires`` names, or none of them.

    Every wire is checked even once one has been refused, so a body comes back with everything wrong
    with it rather than one fault per round trip. The refusals are keyed by the position in ``wires``
    of the wire at fault, under the body's own ``wires`` key -- the shape the body serializer refuses
    a wire in -- so a client parses a refusal from the graph exactly as it parses one from the body.
    Nothing is written either way: the planner runs before the graph is saved, inside the write's
    transaction.

    A wire is checked against the wires before it as well as against the graph, so one body cannot
    wire a pair twice, and no two wires can be handed the same id.

    No node is moved: Phase 1 leaves the canvas alone (W11).
    """
    planned: list[FlowEdge] = []
    refusals: dict[int, dict] = {}
    for index, wire in enumerate(wires):
        try:
            planned.append(_planned_edge(flow, planned, wire))
        except serializers.ValidationError as refusal:
            refusals[index] = refusal.detail
    if refusals:
        raise serializers.ValidationError({WIRES_FIELD: refusals})
    return PipelineEdit(diff=graph_diff(edges=EdgeDiff(add=planned)), written_ids=[edge.id for edge in planned])


def _planned_edge(flow: Flow, planned: list[FlowEdge], wire: dict) -> FlowEdge:
    """The edge one wire asks for, refused if the graph or the wires before it cannot carry it.

    The id is the server's to assign (W5), built with react-flow's own ``getEdgeId`` formula so an
    API-wired edge reads like a hand-wired one. Not byte-identical to one: the UI builder's edges
    carry ``targetHandle: null``, so their ids end at the target's node id where these end in
    ``input``. Ids are opaque, and the duplicate check compares the wiring rather than the id, so
    the two conventions sit side by side in one graph.
    """
    source, target = wire["source"], wire["target"]
    source_node, target_node = _endpoints(flow, source, target)
    from_handle = SourceSide.resolve_handle(source_node, wire.get("source_handle"))
    to_handle = TargetSide.resolve_handle(target_node, wire.get("target_handle"))
    wiring: Wiring = (source, from_handle, target, to_handle)
    _refuse_duplicate(flow, planned, wiring)
    return FlowEdge(
        id=_unused_edge_id(flow, planned, wiring),
        source=source,
        target=target,
        sourceHandle=from_handle,
        targetHandle=to_handle,
    )


def plan_delete(flow: Flow, edge_id: str) -> PipelineEdit:
    """Remove one edge, leaving both nodes it joined exactly where they are.

    Edge ids are addresses, so a wrong one is a wrong URL rather than a bad body -- and a *repeated*
    delete gets that same 404, which is what makes an unwire safe to retry.
    """
    if edge_id not in {edge.id for edge in flow.edges}:
        raise NotFound(f"This pipeline has no edge '{edge_id}'.")
    return PipelineEdit(diff=graph_diff(edges=EdgeDiff(delete=[edge_id])))


def _refuse_duplicate(flow: Flow, planned: list[FlowEdge], wiring: Wiring) -> None:
    """Refuse a wire the graph already holds, or one the same body has already asked for.

    Two edges wiring the same pair from the same handle are one edge said twice: the pipeline follows
    the wire once either way. Refusing the second makes the most retry-prone write in the API safe to
    retry (spec §8.2), and the existing edge's id is in the refusal so a client that never saw the
    first response can carry on without re-reading the graph. A repeat within one body has no id to
    name yet, so it names where the first of the two is instead.
    """
    for edge in flow.edges:
        if edge.wiring == wiring:
            raise _refusal(f"These nodes are already wired this way, by edge '{edge.id}'. Nothing was changed.")
    for index, edge in enumerate(planned):
        if edge.wiring == wiring:
            raise _refusal(f"This body already wires these nodes this way, at index {index}. Nothing was changed.")


def _refusal(message: str) -> serializers.ValidationError:
    """A refusal about a wire as a whole rather than about one of its fields."""
    return serializers.ValidationError({api_settings.NON_FIELD_ERRORS_KEY: [message]})


def _endpoints(flow: Flow, source: str, target: str) -> tuple[FlowNodeData, FlowNodeData]:
    """The content of the two nodes the edge names, or a 400 naming every field at fault.

    400 rather than the 404 a wrong node id in a *path* gets: an endpoint is a field of the body.
    Node ids are unique per pipeline rather than globally, so an id from another chatbot's graph
    lands here too -- and has to, since an edge naming a node the graph cannot resolve breaks cycle
    detection and reachability outright.

    Both are reported at once, since a client working from a stale read of the graph is as likely to
    have both wrong as one.
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


class Side:
    """One end of an edge, and which of that node's handles a wire lands on.

    Both ends answer the same three questions -- what does this node offer, what does an omitted
    handle mean, is the named one on offer -- so the answering lives here and a subclass supplies
    only what differs. Only a router offers a choice today, and only on the source side, but a node
    type accepting more than one input would ask the target the same question.
    """

    #: The body field naming the node at this end, and the one naming its handle.
    endpoint: ClassVar[str]
    handle_field: ClassVar[str]
    #: What this end's handles are called in a sentence.
    kind: ClassVar[str]

    @classmethod
    def resolve_handle(cls, content: FlowNodeData, requested: str | None) -> str:
        """Which of this end's handles the edge lands on.

        An omitted handle is filled in only when the node offers exactly one. A router offers one
        output per branch, and picking a branch nobody named would route traffic somewhere nobody
        chose, so the refusal lists the handles on offer instead.
        """
        offered = cls.handles_offered(content)
        if not offered:
            raise serializers.ValidationError({cls.endpoint: cls.no_handles_message(content)})
        if requested is None:
            if len(offered) > 1:
                raise serializers.ValidationError(
                    {
                        cls.handle_field: (
                            f"'{content.id}' offers more than one {cls.kind} handle; name the one to "
                            f"wire: {', '.join(offered)}."
                        )
                    }
                )
            return offered[0]
        if requested not in offered:
            raise serializers.ValidationError(
                {
                    cls.handle_field: (
                        f"'{requested}' is not an {cls.kind} handle '{content.id}' offers. "
                        f"On offer: {', '.join(offered)}."
                    )
                }
            )
        return requested

    @classmethod
    def handles_offered(cls, content: FlowNodeData) -> list[str]:
        """The handles the node at this end offers, by name."""
        raise NotImplementedError

    @classmethod
    def no_handles_message(cls, content: FlowNodeData) -> str:
        """What to tell a caller that named a node offering none."""
        raise NotImplementedError


class SourceSide(Side):
    """The end an edge leaves from."""

    endpoint = "source"
    handle_field = "source_handle"
    kind = "output"

    @classmethod
    def handles_offered(cls, content: FlowNodeData) -> list[str]:
        return [handle["handle"] for handle in output_handles(content.type, content.params, content.id)]

    @classmethod
    def no_handles_message(cls, content: FlowNodeData) -> str:
        """Why the node offers no output handle, and whether that is the caller's to fix.

        Only one case is, and it is on the documented happy path: the `pipeline_node_create` endpoint
        stores ``keywords: []`` for ``{"type": "RouterNode"}``, and a router's handles *are* its
        keywords, so the one node type ``source_handle`` exists for arrives unwirable. Handed the End
        node's answer instead, an agent would delete the node when the fix is one PATCH away.

        Held as a lookup on :func:`~apps.pipelines.build_state.why_no_output_handles`, so a case
        added there raises ``KeyError`` here instead of quietly inheriting the End node's answer.
        """
        return _NO_OUTPUT_HANDLES_MESSAGES[why_no_output_handles(content.type)].format(id=content.id, type=content.type)


class TargetSide(Side):
    """The end an edge points at.

    ``target_handle`` is never required, since only the Start node lacks an input handle and it
    cannot be a target at all. The field exists so an edge read back from the `chatbot_inspect`
    endpoint can be sent straight back, and so a multi-input node type would need no new field.
    """

    endpoint = "target"
    handle_field = "target_handle"
    kind = "input"

    @classmethod
    def handles_offered(cls, content: FlowNodeData) -> list[str]:
        return input_handles(content.type)

    @classmethod
    def no_handles_message(cls, content: FlowNodeData) -> str:
        return f"'{content.id}' has no input handles, so no edge can point at it."


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


def _unused_edge_id(flow: Flow, planned: list[FlowEdge], wiring: Wiring) -> str:
    """An edge id neither this graph nor an earlier wire in the same body already has.

    The base is the id react-flow's own ``getEdgeId`` draws for these ends, which is not unique on
    its own: a keyword edit can leave an edge holding the id of a wiring it no longer has (see
    :func:`~apps.api.v2.pipeline_edit.graph_editor._rewired_edges`), and the patch engine treats an
    add whose id already exists as a no-op -- which would answer 201 having stored nothing. So a
    taken id gets a suffix, drawn by :func:`.ids.with_free_suffix`; trying the bare base first is the
    whole of what differs from how a node id is drawn.
    """
    source, source_handle, target, target_handle = wiring
    base = f"reactflow__edge-{source}{source_handle}-{target}{target_handle}"
    taken = {edge.id for edge in flow.edges} | {edge.id for edge in planned}
    return base if base not in taken else with_free_suffix(base, taken)
