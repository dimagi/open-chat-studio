"""Request and response shapes for the pipeline façade (#4140, #4141)."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.v2.inspect.serializers import OutputHandleSerializer, PipelineBuildErrorsSerializer
from apps.api.v2.write.base import RejectsUnknownKeys
from apps.pipelines.build_state import node_output_handles
from apps.pipelines.models import Node

from .graph_editor import settable_params

#: Where the ids on a wire body come from -- the one thing an agent cannot guess, since both a node id
#: and a handle name are the server's to assign.
NODE_IDS = "Node ids are the ones a node write returns and `GET /api/v2/chatbots/{id}/inspect/` reports."
HANDLE_NAMES = (
    "Handle names are the source node's own `output_handles`, which every node write returns; "
    "`unwired_handles` lists the ones still free."
)

#: Where a param's name comes from -- the one thing a free-form ``params`` object cannot say for
#: itself. Spelled out on both write bodies, since a client reads one or the other.
PARAM_NAMES = (
    "Param names are the node type's own, as published by "
    "`GET /api/v2/pipeline/nodes/{node_type}/`. They are not always the name of the "
    "`/pipeline/options/` list a param draws its values from -- `source_material_id` is the param, "
    "`source_material` is the list it chooses from -- so send the param name. A name the type does "
    "not declare is dropped rather than refused, and the response reports the params the node "
    "actually ended up holding."
)

#: Keys a client might reasonably try to set on a *node* that the server owns (W5). Named
#: individually because the generic "unrecognised field" answer reads as a typo rather than as a rule.
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

    #: ``key -> why the server owns it``, declared by each serializer that mixes this in. A bare
    #: annotation rather than an empty default, matching ``LeadsWithWhatWasWritten.written_field``:
    #: of the two ways to get this wrong, silently checking nothing is the one that ships a hole,
    #: since the guard's whole job is to refuse a key the client must not set.
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

    type = serializers.CharField(help_text="A node type from GET /api/v2/pipeline/nodes/.")
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

    The same three fields ``GET /chatbots/{id}/inspect/`` publishes, so one shape is parsed across
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

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        return {self.written_field: fields.pop(self.written_field), **fields}


class NodeWriteSerializer(LeadsWithWhatWasWritten):
    """A node write's response: the node as written, then the pipeline's state after the write."""

    written_field = "node"

    node = WrittenNodeSerializer()


class EdgeCreateSerializer(RejectsServerAssignedKeys, RejectsUnknownKeys, serializers.Serializer):
    """The POST body: which two nodes to wire, and from which handle.

    ``source`` and ``target`` are all that is required. A handle left out (or sent as null, the way
    ``/inspect/`` reports the pipeline builder's own edges) means the only one the node has, so it is
    named only when the node offers a choice -- which today is a router, and only on the source side.
    """

    server_assigned_keys = SERVER_ASSIGNED_EDGE_KEYS

    source = serializers.CharField(help_text="``node_id`` the edge leaves from. " + NODE_IDS)
    target = serializers.CharField(help_text="``node_id`` the edge points to.")
    source_handle = serializers.CharField(
        required=False,
        allow_null=True,
        help_text=(
            "Output handle on the source node. Required only when the node offers more than one: a "
            "router exposes one handle per branch (``output_0``, ``output_1``, …, mapping by index "
            "to its ``keywords``), while every other node has a single ``output``. " + HANDLE_NAMES
        ),
    )
    target_handle = serializers.CharField(
        required=False,
        allow_null=True,
        help_text=(
            "Input handle on the target node. Never required, and the only accepted value is "
            "``input``: every node type has exactly that one implicit input handle, bar the Start "
            "node, which has none at all and so cannot be a `target`."
        ),
    )


class WrittenEdgeSerializer(serializers.Serializer):
    """An edge as a write returns it: the same field names ``GET /chatbots/{id}/inspect/`` reports under
    ``pipeline.graph.edges``, so one reader parses both.

    Not the identical schema, though — inspect's handles are nullable because the pipeline builder's
    own edges store a null ``targetHandle``, while a handle on this side is always resolved to a name.
    """

    id = serializers.CharField(help_text="The edge's server-assigned identity: the address DELETE takes.")
    source = serializers.CharField(help_text="``node_id`` the edge leaves from.")
    target = serializers.CharField(help_text="``node_id`` the edge points to.")
    source_handle = serializers.CharField(source="sourceHandle", help_text="Output handle on the source node.")
    target_handle = serializers.CharField(source="targetHandle", help_text="Input handle on the target node.")


class EdgeWriteSerializer(LeadsWithWhatWasWritten):
    """An edge write's response: the edge as written, then the pipeline's state after the write."""

    written_field = "edge"

    edge = WrittenEdgeSerializer()
