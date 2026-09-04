"""Request and response shapes for the pipeline façade (#4140, #4141)."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.v2.inspect.serializers import OutputHandleSerializer, PipelineBuildErrorsSerializer
from apps.api.v2.write.base import RejectsUnknownKeys
from apps.pipelines.build_state import node_output_handles
from apps.pipelines.models import Node

from .graph_editor import settable_params

#: Where a param's name comes from -- the one thing a free-form ``params`` object cannot say for
#: itself. Spelled out on both write bodies, since a client reads one or the other.
PARAM_NAMES = (
    "Param names are the node type's own, as published by the `pipeline_node_retrieve` endpoint. "
    "They are not always the name of the option list a param draws its values from -- "
    "`source_material_id` is the param, `source_material` is the list it chooses from -- so send "
    "the param name. A name the type does not declare is dropped rather than refused, and the "
    "response reports the params the node ended up holding."
)

#: Keys a client might reasonably try to set on a *node* that the server owns (W5). Named
#: individually because the generic "unrecognised field" answer reads as a typo, not as a rule.
SERVER_ASSIGNED_NODE_KEYS = {
    "node_id": "Node ids are assigned by the server and returned in the response; they cannot be chosen.",
    "position": "Node positions are assigned by the server; move a node in the pipeline builder instead.",
}

#: The same for an *edge*. Both spellings, because the response calls it ``id`` (matching what
#: ``/inspect/`` reports) while the path parameter that deletes it is ``edge_id``.
SERVER_ASSIGNED_EDGE_KEYS = {
    key: "Edge ids are assigned by the server and returned in the response; they cannot be chosen."
    for key in ("id", "edge_id")
}


class RejectsServerAssignedKeys:
    """Answer a client-supplied server-owned key with the rule, not with 'no such field'."""

    #: ``key -> why the server owns it``. A bare annotation, so a serializer that mixes this in and
    #: declares nothing raises rather than quietly checking nothing.
    server_assigned_keys: dict[str, str]

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            claimed = {key: reason for key, reason in self.server_assigned_keys.items() if key in data}
            if claimed:
                raise serializers.ValidationError(claimed)
        return super().to_internal_value(data)


class NodeCreateSerializer(RejectsServerAssignedKeys, RejectsUnknownKeys, serializers.Serializer):
    """The POST body: a node type, and what to start it off with.

    ``type`` alone is enough — the server fills in the type's defaults, and whatever is still
    missing is reported rather than refused, so a node can be built up over several calls.
    """

    server_assigned_keys = SERVER_ASSIGNED_NODE_KEYS

    type = serializers.CharField(help_text="A node type the `pipeline_node_list` endpoint serves.")
    label = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Display name shown in the pipeline builder. Defaults to the node type's own label.",
    )
    params = serializers.DictField(
        required=False,
        default=dict,
        help_text=(
            "The node's configuration. Anything omitted takes the type's default, so `type` alone "
            "is a valid body. " + PARAM_NAMES
        ),
    )


class WrittenNodeSerializer(serializers.Serializer):
    """A node as a write returns it.

    Deliberately not the inspect node shape: inspect renders the resolved resources instead of the
    ids in ``params``, whereas this is the shape you can send back.
    """

    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()
    params = serializers.SerializerMethodField()
    output_handles = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DictField())
    def get_params(self, node: Node) -> dict:
        # Narrowed to what a client may send back: a node is stored with a default for every field
        # its type declares, withheld ones included, and reporting one of those would make this
        # response a body PATCH refuses.
        return settable_params(node)

    @extend_schema_field(OutputHandleSerializer(many=True))
    def get_output_handles(self, node: Node) -> list:
        # Server-derived (W5). Returned on every write so the next call can wire an edge from a
        # handle without a re-read.
        return node_output_handles(node)


class NodeUpdateSerializer(RejectsServerAssignedKeys, RejectsUnknownKeys, serializers.Serializer):
    """The PATCH body: the params and label to change, and nothing else.

    ``type`` is absent on purpose — a node's type decides what its params mean, so changing it in
    place would reinterpret every stored value. Delete the node and add one of the other type.
    """

    server_assigned_keys = SERVER_ASSIGNED_NODE_KEYS

    label = serializers.CharField(
        required=False, allow_blank=True, help_text="Display name shown in the pipeline builder."
    )
    params = serializers.DictField(
        required=False,
        default=dict,
        help_text="The params to change; the ones you leave out are left as they are. " + PARAM_NAMES,
    )


class PipelineWriteSerializer(serializers.Serializer):
    """What every façade write reports back about the pipeline it just changed.

    The same three fields the `chatbot_inspect` endpoint publishes, so one shape is parsed across
    read and write.
    """

    pipeline_valid = serializers.BooleanField(
        help_text="Whether the pipeline validates cleanly: all three error buckets empty, and nothing more."
    )
    pipeline_errors = PipelineBuildErrorsSerializer()
    unwired_handles = serializers.DictField(
        child=serializers.ListField(child=OutputHandleSerializer()),
        help_text=(
            "Advisory 'what still needs wiring' map, keyed by ``node_id``: every output handle with "
            "no outgoing edge and every implicit ``input`` handle with no incoming edge. Never an "
            "error and never blocks a publish."
        ),
    )


class LeadsWithWhatWasWritten(PipelineWriteSerializer):
    """Puts the resource this write touched in front of the pipeline state that is its context."""

    #: Name of the field to lead with, declared on the subclass alongside the field itself.
    written_field: str
    #: Whether that field holds a list. One call wires as many edges as it likes; a node write is
    #: always one node.
    written_many: bool = False

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        return {self.written_field: fields.pop(self.written_field), **fields}


class NodeWriteSerializer(LeadsWithWhatWasWritten):
    """A node write's response: the node as written, then the pipeline's state after the write."""

    written_field = "node"

    node = WrittenNodeSerializer()


#: The wire body's one key. Named here because the planner keys its own refusals by it, so a
#: refusal from the graph is positioned like a refusal from this serializer.
WIRES_FIELD = "wires"

#: Most wires one call may carry. Every wire is checked against every wire before it, under the
#: pipeline's row lock, so the planner's cost is the square of the body's length -- an unbounded body
#: would hold that lock against every other writer for as long as it took to walk. Well above any
#: real pipeline's edge count, so a client that meets it is laying out a graph no canvas would hold.
MAX_WIRES_PER_CALL = 100


class EdgeCreateSerializer(RejectsServerAssignedKeys, RejectsUnknownKeys, serializers.Serializer):
    """One entry of the body's ``wires``: which two nodes to wire, and from which handle.

    ``source`` and ``target`` are all that is required. A handle left out (or sent as null, the way
    ``/inspect/`` reports the pipeline builder's own edges) means the only one the node has, so it is
    named only when the node offers a choice -- today a router, and only on the source side.
    """

    server_assigned_keys = SERVER_ASSIGNED_EDGE_KEYS

    source = serializers.CharField(
        help_text=(
            "``node_id`` the edge leaves from. Node ids are the ones a node write returns and the "
            "`chatbot_inspect` endpoint reports."
        )
    )
    target = serializers.CharField(help_text="``node_id`` the edge points to.")
    source_handle = serializers.CharField(
        required=False,
        allow_null=True,
        help_text=(
            "Output handle on the source node. Required only when the node offers more than one: a "
            "router exposes one handle per branch (``output_0``, ``output_1``, …, mapping by index "
            "to its ``keywords``), while every other node has a single ``output``. Every node write "
            "returns the node's `output_handles`; `unwired_handles` lists the ones still free."
        ),
    )
    target_handle = serializers.CharField(
        required=False,
        allow_null=True,
        help_text=(
            "Input handle on the target node. Never required, and ``input`` is the only accepted "
            "value: every node type has that one implicit input handle, bar the Start node, which "
            "has none and so cannot be a `target`."
        ),
    )


class WireBodySerializer(RejectsUnknownKeys, serializers.Serializer):
    """The POST body: the wires to add, under one key.

    ``wires`` holds a list even for a single wire, so a client wiring the node it has just created
    and a client laying out a whole branch send the same shape and the server runs one code path.
    """

    default_error_messages = {
        # What a client sending the wires with nothing around them, or a lone wire, is answered with.
        "invalid": (
            'Expected an object with a "wires" key; got {datatype}. The wires go in a list under '
            "that key, a single wire being a list of one."
        ),
    }

    wires = serializers.ListField(
        child=EdgeCreateSerializer(),
        min_length=1,
        max_length=MAX_WIRES_PER_CALL,
        error_messages={
            "not_a_list": "Expected a list of wires; got {input_type}. A single wire is a list of one.",
            "min_length": "Name at least one wire to add.",
            "max_length": "Name at most {max_length} wires per call; split a larger graph across calls.",
        },
        help_text=(
            "The wires to add, applied in the order given. All of them land or none of them do, and "
            "each edge's `id` comes back in this order."
        ),
    )


class WrittenEdgeSerializer(serializers.Serializer):
    """An edge as a write returns it: the field names the `chatbot_inspect` endpoint reports under
    ``pipeline.graph.edges``, so one reader parses both.

    Not the identical schema: inspect's handles are nullable because the pipeline builder's own edges
    store a null ``targetHandle``, while a handle on this side is always resolved to a name.
    """

    id = serializers.CharField(help_text="The edge's server-assigned identity: the address DELETE takes.")
    source = serializers.CharField(help_text="``node_id`` the edge leaves from.")
    target = serializers.CharField(help_text="``node_id`` the edge points to.")
    source_handle = serializers.CharField(source="sourceHandle", help_text="Output handle on the source node.")
    target_handle = serializers.CharField(source="targetHandle", help_text="Input handle on the target node.")


class EdgeWriteSerializer(LeadsWithWhatWasWritten):
    """A wire call's response: the edges as written, then the pipeline's state after the write."""

    written_field = "edges"
    written_many = True

    edges = WrittenEdgeSerializer(many=True, help_text="The edges this call wired, in the order the body named them.")
